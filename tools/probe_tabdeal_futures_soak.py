"""Five-minute read-only soak test for Tabdeal Futures broadcast."""

import json
import sys

from trade_stream_probe import probe_broadcast


def main():
    result = probe_broadcast(
        symbol="BTC_USDT",
        timeout_seconds=300,
        max_messages=5000,
    )
    valid = int(result.get("valid_trades", 0))
    with_sequence = int(result.get("sequence_present_trades", 0))
    without_sequence = int(result.get("sequence_missing_trades", 0))
    summary = {
        "connected": bool(result.get("connected")),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "messages": int(result.get("messages", 0)),
        "valid_fresh_trades": valid,
        "sequence_present_trades": with_sequence,
        "sequence_missing_trades": without_sequence,
        "sequence_coverage_percent": round(100 * with_sequence / valid, 2) if valid else None,
        "last_diagnostic": str(result.get("last_diagnostic", "unknown"))[:80],
    }
    print("READ_ONLY_FUTURES_SOAK=" + json.dumps(summary, sort_keys=True))
    if not summary["connected"] or valid == 0:
        print("RESULT=SOAK_NOT_CONFIRMED")
        return 2
    print("RESULT=FRESH_FUTURES_STREAM_OBSERVED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
