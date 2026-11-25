"""Read-only probe for whether one Tabdeal trade socket can subscribe to two markets.

This is an empirical compatibility check only. It does not place orders and
must not be treated as proof of a documented exchange contract.
"""
import re
import threading
import time

from trade_stream_probe import BROADCAST_URL, is_trade_timestamp_fresh, parse_trade_message


def classify_multi_symbol_observation(requested_symbols, observed_after_switch):
    """Classify only direct evidence; missing trades remain inconclusive."""
    requested = [str(value).upper() for value in requested_symbols]
    observed = {str(value).upper() for value in observed_after_switch}
    if len(requested) != 2 or len(set(requested)) != 2:
        raise ValueError("exactly two distinct symbols are required")
    if set(requested).issubset(observed):
        return "multi_symbol_confirmed"
    if requested[1] in observed and requested[0] not in observed:
        return "single_symbol_or_replacement_only"
    return "inconclusive"


def probe_multi_symbol_subscription(symbols=("BTC_USDT", "ETH_USDT"),
                                     timeout_seconds=60,
                                     switch_after_seconds=15,
                                     settle_seconds=3):
    """Subscribe to A, then send B on the same socket and observe post-switch trades.

    A positive result requires fresh, strictly parsed trades from BOTH symbols
    after B has been sent and the settle interval has elapsed. A negative or
    inconclusive result never authorizes a production multi-market runner.
    """
    if (not isinstance(symbols, (list, tuple)) or len(symbols) != 2
            or any(not isinstance(value, str)
                   or not re.fullmatch(r"[A-Z0-9]+_USDT", value)
                   for value in symbols)
            or symbols[0] == symbols[1]):
        raise ValueError("symbols must contain two distinct exchange-native USDT pairs")
    for name, value in (("timeout_seconds", timeout_seconds),
                        ("switch_after_seconds", switch_after_seconds),
                        ("settle_seconds", settle_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(name + " must be positive")
    if switch_after_seconds + settle_seconds >= timeout_seconds:
        raise ValueError("timeout must leave an observation window after the switch")

    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc

    lock = threading.RLock()
    state = {
        "connected": False,
        "phase": "before_switch",
        "messages": 0,
        "valid_fresh_messages": 0,
        "before_switch": {},
        "after_switch": {},
        "diagnostics": {},
        "sent_symbols": [],
    }
    timers = []

    def on_open(ws):
        with lock:
            state["connected"] = True
            state["sent_symbols"].append(symbols[0])
        ws.send(symbols[0])

        def send_second():
            if ws.sock and ws.sock.connected:
                with lock:
                    state["phase"] = "settling"
                    state["sent_symbols"].append(symbols[1])
                ws.send(symbols[1])

                def begin_observation():
                    with lock:
                        state["phase"] = "after_switch"
                settle = threading.Timer(float(settle_seconds), begin_observation)
                settle.daemon = True
                timers.append(settle)
                settle.start()

        switch = threading.Timer(float(switch_after_seconds), send_second)
        switch.daemon = True
        timers.append(switch)
        switch.start()

    def on_message(ws, message):
        parsed = parse_trade_message(message)
        with lock:
            state["messages"] += 1
            if not parsed.get("ok"):
                reason = str(parsed.get("reason", "unknown"))
                state["diagnostics"][reason] = state["diagnostics"].get(reason, 0) + 1
                return
            if not is_trade_timestamp_fresh(parsed.get("timestamp_ms")):
                state["diagnostics"]["stale_trade"] = state["diagnostics"].get("stale_trade", 0) + 1
                return
            state["valid_fresh_messages"] += 1
            phase = state["phase"]
            target = "before_switch" if phase == "before_switch" else (
                "after_switch" if phase == "after_switch" else None
            )
            if target:
                symbol = parsed["symbol"]
                state[target][symbol] = state[target].get(symbol, 0) + 1

    def on_error(ws, error):
        with lock:
            state["diagnostics"]["websocket_error"] = type(error).__name__

    app = websocket.WebSocketApp(
        BROADCAST_URL, on_open=on_open, on_message=on_message, on_error=on_error
    )
    timeout = threading.Timer(float(timeout_seconds), app.close)
    timeout.daemon = True
    timeout.start()
    started = time.monotonic()
    try:
        app.run_forever(ping_interval=20, ping_timeout=10)
    finally:
        timeout.cancel()
        for timer in timers:
            timer.cancel()

    with lock:
        before = dict(state["before_switch"])
        after = dict(state["after_switch"])
        result = {
            "connected": state["connected"],
            "symbols_requested": list(symbols),
            "symbols_sent_in_order": list(state["sent_symbols"]),
            "messages": state["messages"],
            "valid_fresh_messages": state["valid_fresh_messages"],
            "before_switch_counts": before,
            "after_switch_counts": after,
            "diagnostics": dict(state["diagnostics"]),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
    result["classification"] = classify_multi_symbol_observation(symbols, after)
    return result
