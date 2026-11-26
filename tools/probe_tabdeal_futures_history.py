"""Read-only discovery probe for possible Tabdeal futures public market-history routes.

This probe is deliberately isolated from the app and paper engine. It sends only
small unauthenticated GET requests, never submits orders, and never prints
trade prices, quantities, account data, or response bodies.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api1.tabdeal.org"
SYMBOLS = ("BTC_USDT", "BTCUSDT")
ROUTES = (
    ("/r/fapi/v1/trades", {"limit": "2"}),
    ("/r/fapi/v1/aggTrades", {"limit": "2"}),
    ("/r/fapi/v1/klines", {"interval": "1m", "limit": "2"}),
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


def probe(path, params):
    for symbol in SYMBOLS:
        query = dict(params)
        query["symbol"] = symbol
        url = BASE + path + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Tabdeal-ReadOnly-MarketHistory-Probe/1.0",
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
                "route": path, "symbol_format": symbol,
                "status": "NETWORK_ERROR",
                "error_type": type(exc).__name__,
            }, sort_keys=True))
            continue

        result = {
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
    for route, params in ROUTES:
        probe(route, params)
