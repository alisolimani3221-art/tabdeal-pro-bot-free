"""Read-only diagnostic for Tabdeal Futures public trade broadcast.

The official docs guarantee the {"trade": {...}} envelope but do not define
fields inside trade. This probe records field names and JSON value types only;
it never logs trade values, reads credentials, or submits orders.
"""
import argparse
import json
import time
import threading

BROADCAST_URL = "wss://api1.tabdeal.org/special_margin/broadcast/"
DEFAULT_SYMBOL = "BTC_USDT"
MAX_MESSAGE_BYTES = 65536


def _type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _shape(value, depth=0):
    """Describe JSON structure without exposing market values."""
    kind = _type_name(value)
    if isinstance(value, dict):
        result = {"type": "object", "keys": {}}
        for key in sorted(value.keys(), key=lambda item: str(item))[:80]:
            child = value[key]
            result["keys"][str(key)] = (
                {"type": "max_depth"} if depth >= 3 else _shape(child, depth + 1)
            )
        return result
    if isinstance(value, list):
        result = {"type": "array", "length": len(value)}
        if value and depth < 3:
            result["item_shape"] = _shape(value[0], depth + 1)
        return result
    return {"type": kind}


def inspect_trade_message(message):
    """Return schema metadata only; never return raw trade values."""
    if isinstance(message, bytes):
        if len(message) > MAX_MESSAGE_BYTES:
            return {"ok": False, "reason": "message_too_large"}
        try:
            message = message.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return {"ok": False, "reason": "invalid_utf8"}
    if not isinstance(message, str) or len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
        return {"ok": False, "reason": "invalid_or_oversized_message"}
    try:
        payload = json.loads(message)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "invalid_json"}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_not_object",
                "payload_type": _type_name(payload)}
    if "error" in payload:
        return {"ok": False, "reason": "exchange_error",
                "top_level_keys": sorted(str(k) for k in payload.keys())[:40]}
    if "trade" not in payload:
        return {"ok": False, "reason": "missing_trade_object",
                "top_level_shape": _shape(payload)}
    trade = payload.get("trade")
    return {
        "ok": isinstance(trade, dict),
        "reason": "trade_object_observed" if isinstance(trade, dict) else "trade_not_object",
        "top_level_shape": _shape(payload),
        "trade_shape": _shape(trade),
    }


def _finite_positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        return None
    return number


# These aliases are retained only for isolated parser unit tests. They are NOT
# treated as the exchange contract until a live observation confirms them.
ALIASES = {
    "price": ("price", "p", "tradePrice", "dealPrice"),
    "quantity": ("quantity", "qty", "q", "volume", "amount"),
    "timestamp": ("timestamp", "time", "T", "tradeTime", "ts"),
}


def _first(mapping, names):
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def parse_trade_message(message, expected_symbol=None):
    """Legacy candidate parser for unit tests; not enabled for candle creation."""
    if isinstance(message, bytes):
        if len(message) > MAX_MESSAGE_BYTES:
            return {"ok": False, "reason": "message_too_large"}
        message = message.decode("utf-8", errors="strict")
    if not isinstance(message, str) or len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
        return {"ok": False, "reason": "invalid_or_oversized_message"}
    try:
        payload = json.loads(message)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "invalid_json"}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_not_object"}
    if "error" in payload:
        return {"ok": False, "reason": "exchange_error"}
    trade = payload.get("trade")
    if not isinstance(trade, dict):
        return {"ok": False, "reason": "missing_trade_object"}
    symbol = trade.get("symbol") or trade.get("s") or payload.get("symbol")
    if symbol is not None and expected_symbol and str(symbol).upper() != expected_symbol.upper():
        return {"ok": False, "reason": "symbol_mismatch"}
    price = _finite_positive(_first(trade, ALIASES["price"]))
    quantity = _finite_positive(_first(trade, ALIASES["quantity"]))
    timestamp = _finite_positive(_first(trade, ALIASES["timestamp"]))
    if price is None or quantity is None or timestamp is None:
        return {"ok": False, "reason": "trade_schema_unrecognized"}
    timestamp_ms = int(timestamp if timestamp >= 100000000000 else timestamp * 1000)
    if timestamp_ms <= 0:
        return {"ok": False, "reason": "invalid_timestamp"}
    return {"ok": True, "symbol": str(symbol or expected_symbol or ""),
            "price": price, "quantity": quantity, "timestamp_ms": timestamp_ms}


def probe_broadcast(symbol=DEFAULT_SYMBOL, timeout_seconds=20, max_messages=20):
    """Connect to public broadcast and collect schema-only observations."""
    if (not isinstance(symbol, str) or not symbol.endswith("_USDT")
            or not symbol.replace("_", "").isalnum()):
        raise ValueError("symbol must look like BTC_USDT")
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc

    result = {"connected": False, "messages": 0, "trade_envelopes": 0,
              "schema_observations": [], "last_diagnostic": "no_message",
              "symbol": symbol}

    def on_open(ws):
        result["connected"] = True
        ws.send(symbol)  # Official protocol: plain-text symbol.

    def on_message(ws, message):
        result["messages"] += 1
        observed = inspect_trade_message(message)
        result["last_diagnostic"] = observed.get("reason", "unknown")
        if observed.get("trade_shape"):
            result["trade_envelopes"] += 1
            signature = json.dumps(observed["trade_shape"], sort_keys=True)
            known = {json.dumps(item, sort_keys=True) for item in result["schema_observations"]}
            if signature not in known and len(result["schema_observations"]) < 5:
                result["schema_observations"].append(observed["trade_shape"])
        if result["messages"] >= max_messages:
            ws.close()

    def on_error(ws, error):
        result["last_diagnostic"] = "websocket_error:" + type(error).__name__

    app = websocket.WebSocketApp(
        BROADCAST_URL, on_open=on_open, on_message=on_message, on_error=on_error
    )
    started = time.monotonic()
    timer = threading.Timer(max(1, int(timeout_seconds)), app.close)
    timer.daemon = True
    timer.start()
    try:
        app.run_forever(ping_interval=20, ping_timeout=10)
    finally:
        timer.cancel()
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    result["schema_confirmed"] = False
    result["note"] = (
        "Schema metadata only. Review observed fields and types before enabling "
        "a strict production parser. No candle aggregation is performed."
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only Tabdeal Futures trade schema probe")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-messages", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(
        probe_broadcast(args.symbol, args.timeout, args.max_messages),
        ensure_ascii=False, sort_keys=True
    ))
