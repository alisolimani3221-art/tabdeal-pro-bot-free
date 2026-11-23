"""Read-only probe for Tabdeal Futures public trade broadcast.

This module never imports account credentials and never submits orders.
The public broadcast payload schema is validated conservatively because the
exchange documentation only guarantees the outer {"trade": {...}} envelope.
"""
import json
import math
import re
import time
import threading
from datetime import datetime, timezone

BROADCAST_URL = "wss://api1.tabdeal.org/special_margin/broadcast/"
DEFAULT_SYMBOL = "AEVO_USDT"
MAX_MESSAGE_BYTES = 65536
# Old broadcast events must never refresh the live-market watchdog or paper marks.
MAX_TRADE_AGE_MS = 30_000


def is_trade_timestamp_fresh(timestamp_ms, now_ms=None, max_age_ms=MAX_TRADE_AGE_MS):
    """Return True only for a recent, non-future epoch-millisecond trade timestamp."""
    if (isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int)
            or timestamp_ms <= 0):
        return False
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms <= 0:
        return False
    if isinstance(max_age_ms, bool) or not isinstance(max_age_ms, int) or max_age_ms < 0:
        return False
    age_ms = now_ms - timestamp_ms
    return 0 <= age_ms <= max_age_ms


ALIASES = {
    "price": ("price", "p", "tradePrice", "dealPrice"),
    "quantity": ("quantity", "qty", "q", "volume", "amount"),
    "timestamp": ("timestamp", "time", "T", "tradeTime", "ts", "updated"),
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


def _parse_timestamp_ms(value):
    """Parse epoch seconds/milliseconds or timezone-aware ISO-8601 timestamps."""
    timestamp_ms = None
    if isinstance(value, str) and not value.strip().replace(".", "", 1).isdigit():
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        utc_value = parsed.astimezone(timezone.utc)
        delta = utc_value - datetime(1970, 1, 1, tzinfo=timezone.utc)
        timestamp_ms = (
            delta.days * 86_400_000
            + delta.seconds * 1000
            + delta.microseconds // 1000
        )
    else:
        numeric = _finite_positive(value)
        if numeric is None:
            return None
        scaled = numeric if numeric >= 100_000_000_000 else numeric * 1000
        if not math.isfinite(scaled):
            return None
        timestamp_ms = int(scaled)

    now_ms = int(time.time() * 1000)
    if (timestamp_ms <= 0 or timestamp_ms >= 10_000_000_000_000
            or timestamp_ms > now_ms + 5 * 60 * 1000):
        return None
    return timestamp_ms


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _first(mapping, names):
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def parse_trade_message(message, expected_symbol=None):
    """Return normalized trade or a safe diagnostic; never guess missing fields."""
    if expected_symbol is not None and (
            not isinstance(expected_symbol, str)
            or not re.fullmatch(r"[A-Za-z0-9]+_USDT", expected_symbol)):
        return {"ok": False, "reason": "invalid_expected_symbol"}
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
        payload = json.loads(message, object_pairs_hook=_reject_duplicate_keys)
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
    # The endpoint is a market-wide broadcast, not a symbol-specific channel.
    # Every event must identify its own symbol so the caller can refresh stream
    # liveness for all valid markets while feeding only the selected market.
    if symbol is None:
        return {"ok": False, "reason": "missing_symbol"}
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9]+_USDT", symbol, re.IGNORECASE):
        return {"ok": False, "reason": "invalid_symbol"}
    if expected_symbol and symbol.upper() != expected_symbol.upper():
        return {"ok": False, "reason": "symbol_mismatch"}
    price = _finite_positive(_first(trade, ALIASES["price"]))
    quantity = _finite_positive(_first(trade, ALIASES["quantity"]))
    raw_timestamp = _first(trade, ALIASES["timestamp"])
    timestamp_ms = _parse_timestamp_ms(raw_timestamp)
    if price is None or quantity is None or raw_timestamp is None:
        return {"ok": False, "reason": "trade_schema_unrecognized",
                "trade_keys": sorted(str(k) for k in trade.keys())[:30]}
    if timestamp_ms is None:
        return {"ok": False, "reason": "invalid_timestamp"}

    sequence = trade.get("sequence")
    if sequence is not None:
        if (isinstance(sequence, bool) or not isinstance(sequence, (int, float))
                or sequence < 0 or sequence > 9_223_372_036_854_775_807):
            return {"ok": False, "reason": "invalid_sequence"}
        if isinstance(sequence, float) and (
                not math.isfinite(sequence) or not sequence.is_integer()):
            return {"ok": False, "reason": "invalid_sequence"}
        sequence = int(sequence)
    side_name = trade.get("side_name")
    if side_name is not None and not isinstance(side_name, str):
        return {"ok": False, "reason": "invalid_side_name"}

    result = {"ok": True, "symbol": symbol.upper(),
              "price": price, "quantity": quantity, "timestamp_ms": timestamp_ms}
    if sequence is not None:
        result["sequence"] = sequence
    if side_name is not None:
        result["side_name"] = side_name.strip().lower()
    return result


def probe_broadcast(symbol=DEFAULT_SYMBOL, timeout_seconds=15, on_trade=None):
    """Connect once, send plain-text symbol on open, and observe public trades."""
    if (not isinstance(symbol, str) or len(symbol) > 32
            or not re.fullmatch(r"[A-Z0-9]+_USDT", symbol)):
        raise ValueError("symbol must be an ASCII USDT futures symbol such as AEVO_USDT")
    if isinstance(timeout_seconds, bool):
        raise ValueError("timeout_seconds must be a finite positive number")
    try:
        timeout_value = float(timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timeout_seconds must be a finite positive number")
    if not math.isfinite(timeout_value) or timeout_value <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc

    result = {"connected": False, "messages": 0, "valid_trades": 0,
              "last_diagnostic": "no_message", "symbol": symbol}

    def on_open(ws):
        # Historical successful Tabdeal probe runs subscribed by sending the
        # plain-text market symbol after the broadcast socket opens. This is
        # not an order or authenticated action; it only selects the read-only
        # market feed. Keep the exact observed wire format (not JSON).
        result["connected"] = True
        ws.send(symbol)

    def on_message(ws, message):
        result["messages"] += 1
        parsed = parse_trade_message(message, expected_symbol=symbol)
        result["last_diagnostic"] = parsed.get("reason", "ok")
        if parsed.get("ok") and is_trade_timestamp_fresh(parsed["timestamp_ms"]):
            result["valid_trades"] += 1
            if on_trade:
                on_trade(parsed)
        elif parsed.get("ok"):
            result["last_diagnostic"] = "stale_trade_timestamp"
        if result["messages"] >= 100:
            ws.close()

    def on_error(ws, error):
        result["last_diagnostic"] = "websocket_error:" + type(error).__name__

    app = websocket.WebSocketApp(
        BROADCAST_URL, on_open=on_open, on_message=on_message, on_error=on_error
    )
    started = time.monotonic()
    timeout = threading.Timer(max(1, int(timeout_value)), app.close)
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
