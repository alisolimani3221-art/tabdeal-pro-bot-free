"""Configurable read-only soak test for Tabdeal Futures broadcast."""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade_stream_probe import probe_broadcast


def _positive_int_env(name, default, maximum):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("{} must be an integer".format(name))
    if isinstance(value, bool) or value < 1 or value > maximum:
        raise ValueError("{} must be between 1 and {}".format(name, maximum))
    return value


def soak_result_is_complete(result, requested_seconds):
    """Require a near-full observation window, not merely an early connection."""
    if not isinstance(result, dict) or isinstance(requested_seconds, bool):
        return False
    try:
        requested = float(requested_seconds)
        elapsed = result.get("elapsed_seconds")
        valid = result.get("valid_trades")
        messages = result.get("messages")
    except (TypeError, ValueError, OverflowError):
        return False
    if (not math.isfinite(requested) or requested <= 0
            or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or isinstance(valid, bool) or not isinstance(valid, int) or valid <= 0
            or isinstance(messages, bool) or not isinstance(messages, int)
            or messages < valid):
        return False
    if result.get("connected") is not True or elapsed < requested * 0.99:
        return False
    diagnostic = result.get("last_diagnostic", "")
    if isinstance(diagnostic, str) and diagnostic.startswith("websocket_error:"):
        return False
    return True


def main():
    try:
        timeout_seconds = _positive_int_env("SOAK_SECONDS", 300, 3600)
        max_messages = _positive_int_env("SOAK_MAX_MESSAGES", 100000, 1000000)
    except ValueError as exc:
        print("CONFIGURATION_ERROR=" + str(exc))
        return 2

    result = probe_broadcast(
        symbol="BTC_USDT",
        timeout_seconds=timeout_seconds,
        max_messages=max_messages,
    )
    valid = result.get("valid_trades", 0)
    with_sequence = result.get("sequence_present_trades", 0)
    without_sequence = result.get("sequence_missing_trades", 0)
    summary = {
        "requested_seconds": timeout_seconds,
        "connected": result.get("connected") is True,
        "elapsed_seconds": result.get("elapsed_seconds"),
        "messages": result.get("messages", 0),
        "valid_fresh_trades": valid,
        "sequence_present_trades": with_sequence,
        "sequence_missing_trades": without_sequence,
        "sequence_coverage_percent": round(100 * with_sequence / valid, 2) if valid else None,
        "last_diagnostic": str(result.get("last_diagnostic", "unknown"))[:80],
    }
    print("READ_ONLY_FUTURES_SOAK=" + json.dumps(summary, sort_keys=True))
    if not soak_result_is_complete(result, timeout_seconds):
        print("RESULT=SOAK_NOT_CONFIRMED")
        return 2
    print("RESULT=FULL_DURATION_FRESH_FUTURES_STREAM_OBSERVED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
