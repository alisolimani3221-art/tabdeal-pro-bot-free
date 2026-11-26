"""Read-only discovery probe for Tabdeal public spot/futures market history.

Only small unauthenticated GET requests are sent. No API keys, account data,
orders, private endpoints, or response prices/quantities are accessed or logged.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api1.tabdeal.org"
SYMBOLS = ("BTC_USDT", "BTCUSDT")
ROUTES = (
    ("futures", "/r/fapi/v1/trades", {"limit": "2"}),
    ("futures", "/r/fapi/v1/aggTrades", {"limit": "2"}),
    ("futures", "/r/fapi/v1/klines", {"interval": "1m", "limit": "2"}),
    ("spot", "/r/api/v1/trades", {"limit": "2"}),
    ("spot", "/r/api/v1/aggTrades", {"limit": "2"}),
    ("spot", "/r/api/v1/klines", {"interval": "1m", "limit": "2"}),
)


def describe_json(payload):
    if isinstance(payload, list):
        first = payload[0] if payload else None
        return {
            "shape": "array",
            "count": len(payload),
            "first_item_type": type(first).__name__ if first is not None else None,
            "first_item_keys": sorted(first.keys()) if isinstance(first, dict) else None,
            "first_item_length": len(first) if isinstance(first, (list, tuple)) else None,
        }
    if isinstance(payload, dict):
        return {
            "shape": "object",
            "keys": sorted(str(key) for key in payload.keys())[:30],
            "has_error": "error" in payload or "code" in payload and "msg" in payload,
        }
    return {"shape": type(payload).__name__}


def probe(market, path, params):
    for symbol in SYMBOLS:
        query = dict(params)
        query["symbol"] = symbol
        url = BASE + path + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Tabdeal-ReadOnly-MarketHistory-Probe/1.1",
                     "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                raw = response.read(262145)
        except urllib.error.HTTPError as exc:
            status = exc.code
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            raw = exc.read(4096)
        except Exception as exc:
            print(json.dumps({
                "market": market, "route": path, "symbol_format": symbol,
                "status": "NETWORK_ERROR", "error_type": type(exc).__name__,
            }, sort_keys=True))
            continue

        result = {
            "market": market,
            "route": path,
            "symbol_format": symbol,
            "http_status": status,
            "content_type": content_type.split(";")[0],
            "body_bytes": len(raw),
            "truncated": len(raw) > 262144,
        }
        try:
            payload = json.loads(raw.decode("utf-8"))
            result.update(describe_json(payload))
        except Exception:
            result["json"] = False
        else:
            result["json"] = True
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    for market, route, params in ROUTES:
        probe(market, route, params)
