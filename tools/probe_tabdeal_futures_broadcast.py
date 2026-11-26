"""Read-only live verification of the public Tabdeal Futures broadcast."""

import json
import sys

from trade_stream_probe import probe_broadcast


def main():
    # BTC_USDT is selected only for a short, unauthenticated market-data probe.
    # The probe reports metadata/counts only; it never prints prices or sizes.
    result = probe_broadcast(symbol="BTC_USDT", timeout_seconds=30)
    summary = {
        "connected": bool(result.get("connected")),
        "messages": int(result.get("messages", 0)),
        "valid_fresh_trades": int(result.get("valid_trades", 0)),
        "last_diagnostic": str(result.get("last_diagnostic", "unknown"))[:80],
        "elapsed_seconds": result.get("elapsed_seconds"),
    }
    print("READ_ONLY_FUTURES_BROADCAST=" + json.dumps(summary, sort_keys=True))
    if not summary["connected"]:
        print("RESULT=WEBSOCKET_CONNECTION_NOT_CONFIRMED")
        return 2
    if summary["valid_fresh_trades"] < 1:
        print("RESULT=FRESH_FUTURES_TRADE_SCHEMA_NOT_CONFIRMED")
        return 2
    print("RESULT=FRESH_FUTURES_TRADE_SCHEMA_CONFIRMED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
