"""Read-only probe for a Tabdeal Futures historical candle endpoint.

This deliberately does not assume Binance compatibility. It reports whether the
exchange's own public read API exposes a validated 1m OHLCV history response.
"""
import json
import math
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api1.tabdeal.org/r/fapi/v1"
CANDIDATES = ("BTC_USDT", "BTCUSDT")


def _valid_klines(payload):
    if not isinstance(payload, list) or len(payload) < 23:
        return False, "response is not a list with at least 23 rows"
    parsed = []
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            return False, "candle row has fewer than six OHLCV fields"
        try:
            stamp = int(row[0])
            values = [float(row[index]) for index in range(1, 6)]
        except (TypeError, ValueError, OverflowError):
            return False, "candle fields are not numeric"
        if stamp <= 0 or stamp % 60000:
            return False, "candle timestamp is not epoch-minute aligned"
        if not all(math.isfinite(value) for value in values):
            return False, "candle OHLCV contains non-finite values"
        op, high, low, close, volume = values
        if min(op, high, low, close) <= 0 or volume < 0:
            return False, "candle prices/volume outside valid range"
        if high < max(op, close, low) or low > min(op, close, high):
            return False, "inconsistent OHLC range"
        parsed.append((stamp, op, high, low, close, volume))
    parsed.sort(key=lambda item: item[0])
    if len({row[0] for row in parsed}) != len(parsed):
        return False, "duplicate candle timestamps"
    if any(parsed[index][0] - parsed[index - 1][0] != 60000
           for index in range(1, len(parsed))):
        return False, "candle history contains a gap"
    return True, "validated {} contiguous 1m candles".format(len(parsed))


def probe(timeout=12):
    reports = []
    for symbol in CANDIDATES:
        query = urllib.parse.urlencode({
            "symbol": symbol, "interval": "1m", "limit": "25"
        })
        url = BASE + "/klines?" + query
        request = urllib.request.Request(
            url, headers={"User-Agent": "FuturesAnalyzer-CandleProbe/1.0",
                          "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                body = response.read(2_000_000).decode("utf-8")
            payload = json.loads(body)
            valid, detail = _valid_klines(payload)
            reports.append({
                "symbol": symbol, "http_status": status,
                "valid": valid, "detail": detail,
                "rows": len(payload) if isinstance(payload, list) else 0,
            })
        except urllib.error.HTTPError as exc:
            reports.append({"symbol": symbol, "http_status": exc.code,
                            "valid": False, "detail": "HTTP error"})
        except Exception as exc:
            reports.append({"symbol": symbol, "http_status": None,
                            "valid": False,
                            "detail": "{}: {}".format(type(exc).__name__, str(exc)[:160])})
    return {
        "endpoint": BASE + "/klines",
        "status": "validated_public_klines" if any(item["valid"] for item in reports)
                  else "no_validated_public_klines",
        "live_orders": False,
        "results": reports,
    }


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True))
