"""Read-only experiment: can one Tabdeal Futures broadcast socket retain two markets?

The only observed subscription wire format is a plain-text symbol sent after
opening the public broadcast socket. This probe first establishes a one-symbol
baseline, then sends a second symbol on the SAME connection and observes which
symbols continue to arrive. It never uses credentials or submits orders.
"""
import json
import math
import re
import threading
import time

import certifi
from android_tls import tabdeal_websocket_ssl_options
from trade_stream_probe import BROADCAST_URL, parse_trade_message, is_trade_timestamp_fresh

DEFAULT_SYMBOLS = ("BTC_USDT", "ETH_USDT")


def probe_multiple_symbols(symbols=DEFAULT_SYMBOLS, timeout_seconds=60):
    if (not isinstance(symbols, (list, tuple)) or len(symbols) != 2
            or any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", s)
                   for s in symbols)
            or symbols[0] == symbols[1]):
        raise ValueError("symbols must contain two distinct uppercase USDT futures symbols")
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

    first, second = symbols
    result = {
        "connected": False,
        "symbols": list(symbols),
        "sent_subscriptions": [],
        "messages": 0,
        "valid_trades_by_symbol": {first: 0, second: 0},
        "post_second_subscription_by_symbol": {first: 0, second: 0},
        "unexpected_symbols": {},
        "diagnostics": {},
        "second_subscription_sent": False,
        "status": "not_started",
    }
    lock = threading.Lock()

    def on_open(ws):
        result["connected"] = True
        # Baseline: send the exact observed plain-text selector for symbol one.
        ws.send(first)
        result["sent_subscriptions"].append(first)

    def on_message(ws, message):
        parsed = parse_trade_message(message)
        with lock:
            result["messages"] += 1
            if not parsed.get("ok"):
                reason = parsed.get("reason", "unknown")
                result["diagnostics"][reason] = result["diagnostics"].get(reason, 0) + 1
                return
            if not is_trade_timestamp_fresh(parsed["timestamp_ms"]):
                result["diagnostics"]["stale_trade_timestamp"] = (
                    result["diagnostics"].get("stale_trade_timestamp", 0) + 1
                )
                return

            symbol = parsed["symbol"]
            if symbol in result["valid_trades_by_symbol"]:
                result["valid_trades_by_symbol"][symbol] += 1
            else:
                result["unexpected_symbols"][symbol] = (
                    result["unexpected_symbols"].get(symbol, 0) + 1
                )

            # Only after the first symbol has produced a valid, fresh event do
            # we send symbol two. This makes the baseline and transition clear.
            if symbol == first and not result["second_subscription_sent"]:
                ws.send(second)
                result["sent_subscriptions"].append(second)
                result["second_subscription_sent"] = True
                return

            if result["second_subscription_sent"] and symbol in result["post_second_subscription_by_symbol"]:
                result["post_second_subscription_by_symbol"][symbol] += 1

            post = result["post_second_subscription_by_symbol"]
            if result["second_subscription_sent"] and all(post[s] > 0 for s in symbols):
                result["status"] = "both_symbols_observed_after_second_subscription"
                ws.close()
            elif result["messages"] >= 500:
                ws.close()

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
    if not result["connected"]:
        result["status"] = "connection_failed"
    elif not result["second_subscription_sent"]:
        result["status"] = "first_symbol_baseline_not_observed"
    elif result["status"] != "both_symbols_observed_after_second_subscription":
        post = result["post_second_subscription_by_symbol"]
        if post[second] > 0 and post[first] == 0:
            result["status"] = "second_symbol_only_after_second_subscription"
        elif post[first] > 0 and post[second] == 0:
            result["status"] = "first_symbol_only_after_second_subscription"
        else:
            result["status"] = "inconclusive_after_second_subscription"
    return result


if __name__ == "__main__":
    print(json.dumps(probe_multiple_symbols(), ensure_ascii=False, sort_keys=True))
