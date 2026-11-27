"""Read-only probe for the candidate Tabdeal Futures kline history route.

This is diagnostic only: one public GET request, no API key, no account access,
and no order/trading endpoints. A successful response is not trusted until its
OHLCV schema and closed-candle timestamps are validated.
"""
import json
import os
import sys
import time

import requests

BASE = "https://api1.tabdeal.org/r/fapi/v1"
SYMBOL = os.environ.get("TABDEAL_PROBE_SYMBOL", "BTC_USDT")
INTERVAL = "1m"
LIMIT = 5


def main():
    url = BASE + "/klines"
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": LIMIT}
    print("READ_ONLY_PROBE=1")
    print("URL_PATH=/r/fapi/v1/klines")
    print("SYMBOL={}".format(SYMBOL))
    try:
        response = requests.get(
            url, params=params, timeout=(8, 15),
            headers={"Accept": "application/json", "User-Agent": "TabdealCandleHistoryProbe/1.0"},
        )
    except requests.RequestException as exc:
        print("RESULT=NETWORK_ERROR")
        print("ERROR={}: {}".format(type(exc).__name__, str(exc)[:240]))
        return 0

    print("HTTP_STATUS={}".format(response.status_code))
    print("CONTENT_TYPE={}".format(response.headers.get("Content-Type", "")))
    body = response.text[:1200]
    if response.status_code != 200:
        print("RESULT=ROUTE_NOT_CONFIRMED")
        print("BODY_PREFIX={}".format(body.replace("\\n", " ")[:500]))
        return 0

    try:
        payload = response.json()
    except ValueError:
        print("RESULT=INVALID_JSON")
        print("BODY_PREFIX={}".format(body.replace("\\n", " ")[:500]))
        return 0

    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("klines", payload.get("data"))
    if not isinstance(rows, list):
        print("RESULT=UNKNOWN_RESPONSE_SHAPE")
        print("JSON_PREFIX={}".format(json.dumps(payload, ensure_ascii=False)[:900]))
        return 0

    valid = []
    errors = []
    for index, row in enumerate(rows):
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            errors.append("row_{}_not_ohlcv_array".format(index))
            continue
        try:
            stamp = int(row[0])
            op, high, low, close, volume = (float(row[i]) for i in range(1, 6))
            if stamp <= 0 or min(op, high, low, close) <= 0 or volume < 0:
                raise ValueError("nonpositive timestamp/price or negative volume")
            if high < max(op, close, low) or low > min(op, close, high):
                raise ValueError("inconsistent OHLC")
            valid.append([stamp, op, high, low, close, volume])
        except (TypeError, ValueError, OverflowError) as exc:
            errors.append("row_{}_{}".format(index, str(exc)[:100]))

    now_ms = int(time.time() * 1000)
    current_bucket = now_ms - now_ms % 60_000
    closed = [row for row in valid if row[0] + 60_000 <= current_bucket]
    closed.sort(key=lambda row: row[0])
    contiguous = all(
        closed[i][0] - closed[i - 1][0] == 60_000
        for i in range(1, len(closed))
    )
    print("RESULT={}".format("OHLCV_SCHEMA_CONFIRMED" if valid and not errors and contiguous else "SCHEMA_NEEDS_REVIEW"))
    print("ROWS_RETURNED={}".format(len(rows)))
    print("ROWS_VALID={}".format(len(valid)))
    print("ROWS_CLOSED={}".format(len(closed)))
    print("CONTIGUOUS_CLOSED={}".format(contiguous))
    print("ROW_ERRORS={}".format(json.dumps(errors[:5])))
    if valid:
        print("LATEST_VALID_OPEN_TIME_MS={}".format(max(row[0] for row in valid)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
