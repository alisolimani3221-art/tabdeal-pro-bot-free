"""Read-only capacity probe for N simultaneous Tabdeal Futures trade subscriptions.

The app rollout remains capped at the last verified count until this probe
observes repeated fresh trades for every requested symbol after all subscribe
frames have been sent on one socket. No credentials or order endpoints are used.
"""
import json
import math
import re
import threading
import time

import certifi
from android_tls import tabdeal_websocket_ssl_options
from trade_stream_probe import (
    BROADCAST_URL, is_trade_timestamp_fresh, parse_trade_message,
)


def validate_capacity_symbols(symbols):
    if not isinstance(symbols, (list, tuple)) or not 2 <= len(symbols) <= 20:
        raise ValueError("symbols must contain 2-20 futures markets")
    normalized = []
    for value in symbols:
        if not isinstance(value, str):
            raise ValueError("every symbol must be a string")
        symbol = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]+_USDT", symbol) or symbol in normalized:
            raise ValueError("symbols must be unique exchange-native USDT contracts")
        normalized.append(symbol)
    return tuple(normalized)


def probe_broadcast_capacity(symbols, timeout_seconds=120):
    symbols = validate_capacity_symbols(symbols)
    if isinstance(timeout_seconds, bool):
        raise ValueError("timeout_seconds must be finite and positive")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timeout_seconds must be finite and positive")
    if not math.isfinite(timeout) or not 30 <= timeout <= 180:
        raise ValueError("timeout_seconds must be between 30 and 180")

    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc

    result = {
        "connected": False,
        "symbols": list(symbols),
        "sent_subscriptions": [],
        "messages": 0,
        "valid_trades_by_symbol": {symbol: 0 for symbol in symbols},
        "post_all_subscriptions_by_symbol": {symbol: 0 for symbol in symbols},
        "unexpected_symbols": {},
        "diagnostics": {},
        "all_subscriptions_sent": False,
        "status": "not_started",
    }
    lock = threading.Lock()

    def on_open(ws):
        result["connected"] = True
        ws.send(symbols[0])
        result["sent_subscriptions"].append(symbols[0])

    def on_message(ws, message):
        parsed = parse_trade_message(message)
        with lock:
            result["messages"] += 1
            if not parsed.get("ok"):
                reason = parsed.get("reason", "unknown")
                result["diagnostics"][reason] = result["diagnostics"].get(reason, 0) + 1
                return
            if not is_trade_timestamp_fresh(parsed.get("timestamp_ms")):
                result["diagnostics"]["stale_trade_timestamp"] = (
                    result["diagnostics"].get("stale_trade_timestamp", 0) + 1
                )
                return
            symbol = parsed["symbol"]
            if symbol not in result["valid_trades_by_symbol"]:
                result["unexpected_symbols"][symbol] = (
                    result["unexpected_symbols"].get(symbol, 0) + 1
                )
                return
            result["valid_trades_by_symbol"][symbol] += 1

            # Establish a live baseline on the first market before sending the
            # remaining independent plain-text subscription frames.
            if (symbol == symbols[0] and not result["all_subscriptions_sent"]):
                for candidate in symbols[1:]:
                    ws.send(candidate)
                    result["sent_subscriptions"].append(candidate)
                result["all_subscriptions_sent"] = True
                return
            if result["all_subscriptions_sent"]:
                result["post_all_subscriptions_by_symbol"][symbol] += 1

    def on_error(ws, error):
        result["diagnostics"]["websocket_error"] = type(error).__name__

    app = websocket.WebSocketApp(
        BROADCAST_URL, on_open=on_open, on_message=on_message, on_error=on_error
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
    post = result["post_all_subscriptions_by_symbol"]
    sustained = result["elapsed_seconds"] >= min(30.0, timeout)
    repeated = all(post[symbol] >= 3 for symbol in symbols)
    if not result["connected"]:
        result["status"] = "connection_failed"
    elif not result["all_subscriptions_sent"]:
        result["status"] = "first_symbol_baseline_not_observed"
    elif sustained and repeated:
        result["status"] = "all_symbols_sustained_after_all_subscriptions"
    elif any(post.values()):
        result["status"] = "partial_or_insufficient_symbol_coverage"
    else:
        result["status"] = "no_valid_trades_after_all_subscriptions"
    return result


if __name__ == "__main__":
    print(json.dumps(probe_broadcast_capacity(
        ("BTC_USDT", "ETH_USDT", "XRP_USDT", "BNB_USDT", "SOL_USDT")
    ), ensure_ascii=False, sort_keys=True))
