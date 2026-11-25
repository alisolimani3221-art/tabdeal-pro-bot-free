"""Read-only Futures depth stream format matrix for Tabdeal.

The SDK's spot example is not assumed to prove Futures wire compatibility.
A stream format is considered working only after an actual depth frame arrives.
"""
import json
import re
import threading

import certifi
from android_tls import tabdeal_websocket_ssl_options

STREAM_URL = "wss://api1.tabdeal.org/special_margin/stream/"
SYMBOL_FORMS = ("btcusdt", "BTCUSDT", "BTC_USDT", "btc_usdt")
CHANNELS = ("depth@2000ms", "depth@100ms", "depth5@100ms", "depth10@100ms")


def build_candidates(symbol="BTC_USDT"):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
        raise ValueError("symbol must be an exchange-native USDT futures symbol")
    base = symbol[:-5]
    candidates = []
    for form in SYMBOL_FORMS:
        if form == "btcusdt":
            prefix = base.lower() + "usdt"
        elif form == "BTCUSDT":
            prefix = base + "USDT"
        elif form == "BTC_USDT":
            prefix = symbol
        else:
            prefix = symbol.lower()
        for channel in CHANNELS:
            candidates.append(prefix + "@" + channel)
    return candidates


def _normalize_symbol(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([A-Z0-9]+)_?USDT", value.strip().upper())
    return match.group(1) + "_USDT" if match else None


def _depth_data(payload):
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    bids = data.get("bids", data.get("b"))
    asks = data.get("asks", data.get("a"))
    if isinstance(bids, list) and isinstance(asks, list):
        return data
    return None


def _ack_error(payload):
    if not isinstance(payload, dict) or "id" not in payload or "result" not in payload:
        return None
    result = str(payload.get("result"))[:400]
    if "INVALID_FORMAT" in result.upper():
        return "INVALID_FORMAT"
    if "invalid" in result.lower() or "error" in result.lower():
        return result
    return None


def _format_for_symbol(template_stream, symbol):
    template, channel = template_stream.split("@", 1)
    base = symbol[:-5]
    if template.islower() and "_" not in template:
        prefix = base.lower() + "usdt"
    elif template.isupper() and "_" not in template:
        prefix = base + "USDT"
    elif template.islower() and "_" in template:
        prefix = symbol.lower()
    else:
        prefix = symbol
    return prefix + "@" + channel


def _observe(streams, symbols, timeout_seconds):
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc
    result = {
        "connected": False, "streams": list(streams), "symbols": list(symbols),
        "ack": False, "ack_error": None, "messages": 0,
        "depth_frames_by_symbol": {s: 0 for s in symbols},
        "unattributed_depth_frames": 0, "status": "not_started",
    }

    def on_open(ws):
        result["connected"] = True
        ws.send(json.dumps({"method": "SUBSCRIBE", "id": 7, "params": list(streams)},
                           separators=(",", ":")))

    def on_message(ws, message):
        try:
            payload = json.loads(message.decode("utf-8") if isinstance(message, bytes) else message)
        except (TypeError, ValueError, UnicodeDecodeError):
            return
        result["messages"] += 1
        error = _ack_error(payload)
        if isinstance(payload, dict) and payload.get("id") == 7 and "result" in payload:
            result["ack"] = True
            result["ack_error"] = error
        data = _depth_data(payload)
        if data is None:
            return
        symbol = _normalize_symbol(data.get("s", data.get("symbol")))
        if symbol in result["depth_frames_by_symbol"]:
            result["depth_frames_by_symbol"][symbol] += 1
        else:
            result["unattributed_depth_frames"] += 1

    def on_error(ws, error):
        result["transport_error"] = type(error).__name__

    app = websocket.WebSocketApp(STREAM_URL, on_open=on_open,
                                 on_message=on_message, on_error=on_error)
    timer = threading.Timer(float(timeout_seconds), app.close)
    timer.daemon = True
    timer.start()
    try:
        app.run_forever(ping_interval=20, ping_timeout=10, reconnect=0,
                        sslopt=tabdeal_websocket_ssl_options(certifi.where()))
    finally:
        timer.cancel()
    counts = result["depth_frames_by_symbol"]
    if not result["connected"]:
        result["status"] = "connection_failed"
    elif result["ack_error"]:
        result["status"] = "exchange_rejected_format"
    elif len(symbols) == 1 and counts[symbols[0]] > 0:
        result["status"] = "single_symbol_depth_observed"
    elif len(symbols) > 1 and all(counts[s] > 0 for s in symbols):
        result["status"] = "all_symbol_depths_observed"
    elif result["ack"]:
        result["status"] = "ack_without_depth"
    else:
        result["status"] = "no_ack_or_depth"
    return result


def probe_futures_depth_formats(symbols=("BTC_USDT", "ETH_USDT"),
                                timeout_seconds=5):
    if (not isinstance(symbols, (list, tuple)) or len(symbols) != 2
            or any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", s)
                   for s in symbols) or symbols[0] == symbols[1]):
        raise ValueError("exactly two distinct exchange-native USDT symbols are required")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 15:
        raise ValueError("timeout_seconds must be between 1 and 15")
    trials = []
    working = None
    for candidate in build_candidates(symbols[0]):
        trial = _observe([candidate], [symbols[0]], timeout_seconds)
        trials.append(trial)
        if trial["status"] == "single_symbol_depth_observed":
            working = candidate
            break
    result = {"symbols": list(symbols), "single_symbol_trials": trials,
              "working_single_symbol_stream": working,
              "multi_symbol_result": None,
              "status": "single_symbol_format_not_verified"}
    if working is None:
        return result
    second = _format_for_symbol(working, symbols[1])
    multi = _observe([working, second], list(symbols), timeout_seconds * 2)
    result["multi_symbol_result"] = multi
    result["status"] = multi["status"]
    return result


if __name__ == "__main__":
    print(json.dumps(probe_futures_depth_formats(), sort_keys=True))
