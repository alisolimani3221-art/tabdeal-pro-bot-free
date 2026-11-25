"""Read-only baseline and multi-market Tabdeal Futures depth WebSocket probe."""
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


def _normalize_ws_symbol(value):
    """Normalize explicit WS ids (e.g. btcusdt) to REST form (BTC_USDT)."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([A-Z0-9]+)_?USDT", value.strip().upper())
    return match.group(1) + "_USDT" if match else None


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
        symbol = _normalize_ws_symbol(payload.get(key))
        if symbol:
            return symbol
    for key in ("stream", "topic", "channel"):
        value = payload.get(key)
        if isinstance(value, str):
            match = re.match(r"^([A-Z0-9]+_?USDT)@", value.upper())
            if match:
                symbol = _normalize_ws_symbol(match.group(1))
                if symbol:
                    return symbol
    for key in ("data", "result", "payload", "orderBook", "orderbook", "depth"):
        found = _find_symbol(payload.get(key))
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
    # The official SDK's websocket example uses compact lowercase ids such as
    # "btcusdt", although REST exchangeInfo uses "BTC_USDT".
    return {
        "method": "SUBSCRIBE",
        "id": request_id,
        "params": [symbol.replace("_", "").lower() + "@depth@2000ms" for symbol in symbols],
    }


def _frame_summary(payload):
    if not isinstance(payload, dict):
        return {"frame_type": type(payload).__name__}
    summary = {"top_level_keys": sorted(str(k) for k in payload.keys())[:24]}
    for key in ("id", "result", "code", "msg", "message", "event", "e", "stream", "symbol", "s"):
        if key in payload:
            value = payload[key]
            summary[key] = str(value)[:100] if value is not None else None
    for key in ("data", "payload", "result", "depth", "orderBook", "orderbook"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            summary[key + "_keys"] = sorted(str(k) for k in nested.keys())[:24]
            for symbol_key in ("symbol", "s"):
                if symbol_key in nested:
                    summary[key + "_" + symbol_key] = str(nested[symbol_key])[:80]
            break
    return summary


def _observe_subscription(symbols, timeout_seconds, request_id=1):
    payload = build_multi_depth_subscription(tuple(symbols), request_id=request_id)
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
        "ack_frames": 0,
        "frame_samples": [],
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
                if len(result["frame_samples"]) < 5:
                    result["frame_samples"].append({"frame_type": "invalid_json"})
                return
            if len(result["frame_samples"]) < 5:
                result["frame_samples"].append(_frame_summary(parsed))
            if isinstance(parsed, dict) and "id" in parsed and "result" in parsed:
                result["ack_frames"] += 1
            if isinstance(parsed, dict) and ("error" in parsed or (
                    "code" in parsed and parsed.get("code") not in
                    (0, "0", 200, "200", "OK", "ok"))):
                result["diagnostics"]["exchange_error"] = result["diagnostics"].get("exchange_error", 0) + 1
                return
            try:
                parse_order_book(parsed)
            except (TypeError, ValueError, OverflowError) as exc:
                reason = "ack_or_unrecognized_frame"
                result["diagnostics"][reason] = result["diagnostics"].get(reason, 0) + 1
                result["diagnostics"]["last_parse_error"] = str(exc)[:160]
                return
            symbol = _find_symbol(parsed)
            if symbol is None and len(expected) == 1:
                # A one-channel control can safely attribute a symbol-less book.
                symbol = expected[0]
                result["unattributed_valid_books"] += 1
            if symbol in result["valid_books_by_symbol"]:
                result["valid_books_by_symbol"][symbol] += 1
                if all(result["valid_books_by_symbol"][s] > 0 for s in expected):
                    result["status"] = "all_subscribed_books_observed"
                    ws.close()
            else:
                result["unattributed_valid_books"] += 1

    def on_error(ws, error):
        result["diagnostics"]["websocket_error"] = type(error).__name__ + ": " + str(error)[:120]

    app = websocket.WebSocketApp(
        STREAM_URL, on_open=on_open, on_message=on_message, on_error=on_error
    )
    previous_timeout = websocket.getdefaulttimeout()
    timer = threading.Timer(timeout_seconds, app.close)
    timer.daemon = True
    started = time.monotonic()
    timer.start()
    websocket.setdefaulttimeout(min(15.0, timeout_seconds))
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
    elif result["status"] != "all_subscribed_books_observed":
        if result["messages"] == 0:
            result["status"] = "connected_no_frames"
        elif result["ack_frames"] and not any(result["valid_books_by_symbol"].values()):
            result["status"] = "subscription_ack_without_depth_updates"
        else:
            result["status"] = "one_or_more_subscribed_books_missing"
    return result


def probe_multi_market_order_book(symbols=DEFAULT_SYMBOLS, timeout_seconds=90):
    if (not isinstance(symbols, (list, tuple)) or len(symbols) < 2
            or len(symbols) > 20 or len(set(symbols)) != len(symbols)):
        raise ValueError("symbols must contain 2-20 unique USDT futures symbols")
    if isinstance(timeout_seconds, bool):
        raise ValueError("timeout_seconds must be finite and positive")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timeout_seconds must be finite and positive")
    if not math.isfinite(timeout) or timeout < 2 or timeout > 180:
        raise ValueError("timeout_seconds must be between 2 and 180")

    baseline_timeout = min(30.0, timeout / 3.0)
    multi_timeout = timeout - baseline_timeout
    baseline = _observe_subscription((symbols[0],), baseline_timeout, request_id=1)
    multi = _observe_subscription(tuple(symbols), multi_timeout, request_id=2)
    result = {
        "connected": bool(baseline["connected"] and multi["connected"]),
        "symbols": list(symbols),
        "single_symbol_baseline": baseline,
        "multi_symbol_subscription": multi,
        "subscription_payload": multi["subscription_payload"],
        "messages": baseline["messages"] + multi["messages"],
        "valid_books_by_symbol": multi["valid_books_by_symbol"],
        "diagnostics": {
            "single_symbol": baseline["diagnostics"],
            "multi_symbol": multi["diagnostics"],
        },
        "status": "not_started",
    }
    if not baseline["connected"] or not multi["connected"]:
        result["status"] = "connection_failed"
    elif baseline["status"] != "all_subscribed_books_observed":
        result["status"] = "single_symbol_baseline_failed"
    elif multi["status"] == "all_subscribed_books_observed":
        result["status"] = "both_symbol_books_observed"
    else:
        result["status"] = "multi_symbol_subscription_missing_books"
    return result


if __name__ == "__main__":
    print(json.dumps(probe_multi_market_order_book(), ensure_ascii=False, sort_keys=True))
