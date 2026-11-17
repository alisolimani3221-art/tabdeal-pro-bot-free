"""Read-only diagnostic for Tabdeal Futures public trade broadcast.

The official docs guarantee the {"trade": {...}} envelope but do not define
fields inside trade. This probe records field names and JSON value types only;
it never logs trade values, reads credentials, or submits orders.
"""
import argparse
import json
import re
import math
from datetime import datetime
from decimal import Decimal, InvalidOperation
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


def _decimal_profile(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[+-]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)", value):
        return {"valid_decimal_string": False}
    try:
        number = Decimal(value)
    except InvalidOperation:
        return {"valid_decimal_string": False}
    if not number.is_finite():
        return {"valid_decimal_string": False}
    fraction = value.lstrip("+-").partition(".")[2]
    return {"valid_decimal_string": True, "positive": number > 0,
            "decimal_places": len(fraction)}


def _timestamp_profile(value):
    if not isinstance(value, str):
        return {"format": "not_string"}
    if re.fullmatch(r"[0-9]{10}", value):
        return {"format": "epoch_seconds_string"}
    if re.fullmatch(r"[0-9]{13}", value):
        return {"format": "epoch_milliseconds_string"}
    if re.fullmatch(r"[0-9]{16}", value):
        return {"format": "epoch_microseconds_string"}
    if re.fullmatch(r"[0-9]{19}", value):
        return {"format": "epoch_nanoseconds_string"}
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return {"format": "other_string"}
    fraction = re.search(r"\\.([0-9]+)", value)
    aware = parsed.tzinfo is not None and parsed.utcoffset() is not None
    return {"format": "iso8601_datetime", "timezone_aware": aware,
            "fractional_digits": len(fraction.group(1)) if fraction else 0}


def _trade_profile(trade, expected_symbol=None):
    """Classify field formats without retaining or returning market values."""
    profile = {}
    for field in ("price", "amount"):
        profile[field] = _decimal_profile(trade.get(field))
    sequence = trade.get("sequence")
    profile["sequence"] = {
        "type": _type_name(sequence),
        "finite": (isinstance(sequence, int) and not isinstance(sequence, bool)) or
                  (isinstance(sequence, float) and math.isfinite(sequence)),
        "integer": isinstance(sequence, int) or
                   (isinstance(sequence, float) and math.isfinite(sequence)
                    and sequence.is_integer()),
    }
    side = trade.get("side_name")
    if isinstance(side, str):
        normalized_side = side.strip().lower()
        if normalized_side in ("buy", "bid", "long"):
            side_class = "buy_like"
        elif normalized_side in ("sell", "ask", "short"):
            side_class = "sell_like"
        else:
            side_class = "unknown"
        profile["side_name"] = {"type": "string", "classification": side_class}
    else:
        profile["side_name"] = {"type": _type_name(side),
                                "classification": "not_string"}
    symbol = trade.get("symbol")
    profile["symbol"] = {
        "type": _type_name(symbol),
        "matches_requested": (symbol == expected_symbol)
                              if expected_symbol is not None else None,
    }
    profile["updated"] = _timestamp_profile(trade.get("updated"))
    return profile


def inspect_trade_message(message, expected_symbol=None):
    """Return schema metadata only; never return raw trade values."""
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
        message_size = len(message.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        return {"ok": False, "reason": "invalid_unicode"}
    if message_size > MAX_MESSAGE_BYTES:
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
    if not isinstance(trade, dict):
        return {"ok": False, "reason": "trade_not_object",
                "top_level_shape": _shape(payload)}
    if not trade:
        return {"ok": False, "reason": "empty_trade_object",
                "top_level_shape": _shape(payload)}
    return {"ok": True, "reason": "trade_object_observed",
            "top_level_shape": _shape(payload), "trade_shape": _shape(trade),
            "trade_profile": _trade_profile(trade, expected_symbol)}


def probe_broadcast(symbol=DEFAULT_SYMBOL, timeout_seconds=20, max_messages=20):
    """Connect to public broadcast and collect schema-only observations."""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
        raise ValueError("symbol must look like BTC_USDT")
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is not installed") from exc

    result = {"connected": False, "messages": 0, "trade_envelopes": 0,
              "schema_observations": [], "field_profiles": [],
              "last_diagnostic": "no_message", "symbol": symbol,
              "connection_error": None}

    def on_open(ws):
        result["connected"] = True
        ws.send(symbol)  # Official protocol: plain-text symbol.

    def on_message(ws, message):
        result["messages"] += 1
        observed = inspect_trade_message(message, expected_symbol=symbol)
        result["last_diagnostic"] = observed.get("reason", "unknown")
        if observed.get("ok") and observed.get("trade_shape"):
            result["trade_envelopes"] += 1
            signature = json.dumps(observed["trade_shape"], sort_keys=True)
            known = {json.dumps(item, sort_keys=True) for item in result["schema_observations"]}
            if signature not in known and len(result["schema_observations"]) < 5:
                result["schema_observations"].append(observed["trade_shape"])
            profile_signature = json.dumps(observed["trade_profile"], sort_keys=True)
            known_profiles = {json.dumps(item, sort_keys=True)
                              for item in result["field_profiles"]}
            if profile_signature not in known_profiles and len(result["field_profiles"]) < 5:
                result["field_profiles"].append(observed["trade_profile"])
        if result["messages"] >= max_messages:
            ws.close()

    def on_error(ws, error):
        result["last_diagnostic"] = "websocket_error:" + type(error).__name__
        result["connection_error"] = type(error).__name__

    app = websocket.WebSocketApp(
        BROADCAST_URL, on_open=on_open, on_message=on_message, on_error=on_error
    )
    started = time.monotonic()
    timer = threading.Timer(max(1, int(timeout_seconds)), app.close)
    timer.daemon = True
    timer.start()
    try:
        app.run_forever(ping_interval=20, ping_timeout=10)
    except Exception as exc:
        result["connection_error"] = type(exc).__name__
        result["last_diagnostic"] = "websocket_exception:" + type(exc).__name__
    finally:
        timer.cancel()
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    if result["trade_envelopes"] > 0:
        result["probe_status"] = "observed_trade_object"
    elif result["connected"]:
        result["probe_status"] = "connected_no_trade_object"
    else:
        result["probe_status"] = "connection_failed"
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
    parser.add_argument("--output", default=None,
                        help="Optional path for a redacted JSON diagnostic report")
    parser.add_argument("--require-trade", action="store_true",
                        help="Exit non-zero unless a trade object was observed")
    args = parser.parse_args()
    if args.timeout < 1 or args.max_messages < 1:
        parser.error("--timeout and --max-messages must be positive integers")
    report = probe_broadcast(args.symbol, args.timeout, args.max_messages)
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        from pathlib import Path
        output_path = Path(args.output)
        temporary_path = output_path.with_name(output_path.name + ".tmp")
        temporary_path.write_text(rendered + "\n", encoding="utf-8")
        temporary_path.replace(output_path)
    print(rendered)
    if args.require_trade and report["trade_envelopes"] == 0:
        raise SystemExit(2)
