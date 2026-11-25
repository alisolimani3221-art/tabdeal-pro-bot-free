"""Read-only probe for the official Tabdeal multi-market depth SUBSCRIBE payload."""
import json
import math
import re
import threading
import time

import certifi
from android_tls import tabdeal_websocket_ssl_options
from orderbook import parse_order_book

STREAM_URL = "wss://api1.tabdeal.org/special_margin/stream/"
DEFAULT_SYMBOLS = ("BTC_USDT", "ETH_USDT")


def _find_symbol(payload):
    """Find only explicit exchange symbol/stream identifiers; never infer from prices."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    for key in ("symbol", "s"):
        value = payload.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Z0-9]+_USDT", value.upper()):
            return value.upper()
    for key in ("stream", "topic", "channel"):
        value = payload.get(key)
        if isinstance(value, str):
            match = re.match(r"^([A-Z0-9]+_USDT)@", value.upper())
            if match:
                return match.group(1)
    for key in ("data", "result", "payload", "orderBook", "orderbook", "depth"):
        nested = payload.get(key)
        found = _find_symbol(nested)
        if found:
            return found
    return None


def build_multi_depth_subscription(symbols=DEFAULT_SYMBOLS, request_id=1):
    if (not isinstance(symbols, (list, tuple)) or not symbols or len(symbols) > 20
            or any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", s)
                   for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("symbols must be 1-20 unique uppercase USDT futures symbols")
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
        raise ValueError("request_id must be a positive integer")
    # Exact method/params shape used by the official tabdeal-python SDK.
    return {
        "method": "SUBSCRIBE",
        "id": request_id,
        "params": [symbol + "@depth@2000ms" for symbol in symbols],
    }


def probe_multi_market_order_book(symbols=DEFAULT_SYMBOLS, timeout_seconds=90):
    payload = build_multi_depth_subscription(symbols)
    if isinstance(timeout_seconds, bool):
        raise ValueError("timeout_seconds must be finite and positive")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timeout_seconds must be finite and positive")
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 180:
        raise ValueError("timeout_seconds must be between 0 and 180")
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc

    expected = list(symbols)
    result = {
        "connected": False,
        "symbols": expected,
        "subscription_payload": payload,
        "messages": 0,
        "valid_books_by_symbol": {symbol: 0 for symbol in expected},
        "unattributed_valid_books": 0,
        "diagnostics": {},
        "status": "not_started",
    }
    lock = threading.Lock()

    def on_open(ws):
        result["connected"] = True
        ws.send(json.dumps(payload, separators=(",", ":")))

    def on_message(ws, message):
        with lock:
            result["messages"] += 1
            try:
                parsed = json.loads(message.decode("utf-8") if isinstance(message, bytes) else message)
            except (TypeError, ValueError, UnicodeDecodeError):
                result["diagnostics"]["invalid_json"] = result["diagnostics"].get("invalid_json", 0) + 1
                return
            if isinstance(parsed, dict) and ("error" in parsed or "code" in parsed and parsed.get("code") not in (0, "0", 200, "200", "OK")):
                result["diagnostics"]["exchange_error"] = result["diagnostics"].get("exchange_error", 0) + 1
                return
            try:
                parse_order_book(parsed)
            except (TypeError, ValueError, OverflowError):
                reason = "ack_or_unrecognized_frame"
                result["diagnostics"][reason] = result["diagnostics"].get(reason, 0) + 1
                return
            symbol = _find_symbol(parsed)
            if symbol in result["valid_books_by_symbol"]:
                result["valid_books_by_symbol"][symbol] += 1
                if all(result["valid_books_by_symbol"][s] > 0 for s in expected):
                    result["status"] = "both_symbol_books_observed"
                    ws.close()
            else:
                result["unattributed_valid_books"] += 1

    def on_error(ws, error):
        result["diagnostics"]["websocket_error"] = type(error).__name__

    app = websocket.WebSocketApp(
        STREAM_URL, on_open=on_open, on_message=on_message, on_error=on_error
    )
    previous_timeout = websocket.getdefaulttimeout()
    timer = threading.Timer(timeout, app.close)
    timer.daemon = True
    started = time.monotonic()
    timer.start()
    websocket.setdefaulttimeout(min(15.0, timeout))
    try:
        app.run_forever(
            ping_interval=20, ping_timeout=10, reconnect=0,
            sslopt=tabdeal_websocket_ssl_options(certifi.where()),
        )
    finally:
        websocket.setdefaulttimeout(previous_timeout)
        timer.cancel()
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    if not result["connected"]:
        result["status"] = "connection_failed"
    elif result["status"] != "both_symbol_books_observed":
        count = sum(result["valid_books_by_symbol"].values())
        if count == 0:
            result["status"] = "no_valid_book_observed"
        else:
            result["status"] = "one_or_more_symbols_missing"
    return result


if __name__ == "__main__":
    print(json.dumps(probe_multi_market_order_book(), ensure_ascii=False, sort_keys=True))
