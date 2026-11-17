"""Read-only probe for Tabdeal Futures public trade broadcast.

This module never imports account credentials and never submits orders.
The public broadcast payload schema is validated conservatively because the
exchange documentation only guarantees the outer {"trade": {...}} envelope.
"""
import json
import math
import time
import threading

BROADCAST_URL = "wss://api1.tabdeal.org/special_margin/broadcast/"
DEFAULT_SYMBOL = "AEVO_USDT"
MAX_MESSAGE_BYTES = 65536

ALIASES = {
    "price": ("price", "p", "tradePrice", "dealPrice"),
    "quantity": ("quantity", "qty", "q", "volume", "amount"),
    "timestamp": ("timestamp", "time", "T", "tradeTime", "ts"),
}


def _finite_positive(value):
    # bool is a subclass of int in Python; reject it before float conversion.
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _first(mapping, names):
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def parse_trade_message(message, expected_symbol=None):
    """Return normalized trade or a safe diagnostic; never guess missing fields."""
    if isinstance(message, bytes):
        if len(message) > MAX_MESSAGE_BYTES:
            return {"ok": False, "reason": "message_too_large"}
        try:
            message = message.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return {"ok": False, "reason": "invalid_utf8"}
    if not isinstance(message, str):
        return {"ok": False, "reason": "invalid_or_oversized_message"}
    try:
        if len(message.encode("utf-8", errors="strict")) > MAX_MESSAGE_BYTES:
            return {"ok": False, "reason": "message_too_large"}
    except UnicodeEncodeError:
        return {"ok": False, "reason": "invalid_utf8"}
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
        return {"ok": False, "reason": "missing_trade_object",
                "top_level_keys": sorted(str(k) for k in payload.keys())[:20]}

    symbol = trade.get("symbol") or trade.get("s") or payload.get("symbol")
    if symbol is not None and expected_symbol and str(symbol).upper() != expected_symbol.upper():
        return {"ok": False, "reason": "symbol_mismatch"}
    price = _finite_positive(_first(trade, ALIASES["price"]))
    quantity = _finite_positive(_first(trade, ALIASES["quantity"]))
    timestamp = _finite_positive(_first(trade, ALIASES["timestamp"]))
    if price is None or quantity is None or timestamp is None:
        return {"ok": False, "reason": "trade_schema_unrecognized",
                "trade_keys": sorted(str(k) for k in trade.keys())[:30]}
    # Accept seconds or milliseconds, but reject values that overflow during
    # seconds-to-milliseconds conversion before converting to int.
    timestamp_value = timestamp if timestamp >= 100000000000 else timestamp * 1000
    if not math.isfinite(timestamp_value):
        return {"ok": False, "reason": "invalid_timestamp"}
    timestamp_ms = int(timestamp_value)
    if timestamp_ms <= 0:
        return {"ok": False, "reason": "invalid_timestamp"}
    return {"ok": True, "symbol": str(symbol or expected_symbol or ""),
            "price": price, "quantity": quantity, "timestamp_ms": timestamp_ms}


def probe_broadcast(symbol=DEFAULT_SYMBOL, timeout_seconds=15, on_trade=None):
    """Connect once, send plain-text symbol on open, and observe public trades."""
    if not isinstance(symbol, str) or not symbol.endswith("_USDT") or not symbol.replace("_", "").isalnum():
        raise ValueError("symbol must be a USDT futures symbol such as AEVO_USDT")
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc

    result = {"connected": False, "messages": 0, "valid_trades": 0,
              "last_diagnostic": "no_message", "symbol": symbol}

    def on_open(ws):
        result["connected"] = True
        ws.send(symbol)  # Documented broadcast protocol: plain text, not JSON.

    def on_message(ws, message):
        result["messages"] += 1
        parsed = parse_trade_message(message, expected_symbol=symbol)
        result["last_diagnostic"] = parsed.get("reason", "ok")
        if parsed.get("ok"):
            result["valid_trades"] += 1
            if on_trade:
                on_trade(parsed)
        if result["messages"] >= 100:
            ws.close()

    def on_error(ws, error):
        result["last_diagnostic"] = "websocket_error:" + type(error).__name__

    app = websocket.WebSocketApp(
        BROADCAST_URL, on_open=on_open, on_message=on_message, on_error=on_error
    )
    started = time.monotonic()
    timeout = threading.Timer(max(1, int(timeout_seconds)), app.close)
    timeout.daemon = True
    timeout.start()
    try:
        app.run_forever(ping_interval=20, ping_timeout=10)
    finally:
        timeout.cancel()
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result


if __name__ == "__main__":
    print(json.dumps(probe_broadcast(), ensure_ascii=False, sort_keys=True))
