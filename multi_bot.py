"""Read-only multi-market monitor for Tabdeal Futures (no trading)."""
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime

from market_symbols import list_market_symbols
from orderbook import parse_order_book

BASE = "https://api1.tabdeal.org/r/fapi/v1"


def get_data(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Tabdeal-Futures-Monitor/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("{} returned HTTP {}".format(path, response.status))
        return json.loads(response.read().decode("utf-8"))


def get_symbols():
    data = get_data("/exchangeInfo")
    if isinstance(data, dict):
        symbols = data.get("symbols")
        if symbols is None and isinstance(data.get("data"), dict):
            symbols = data["data"].get("symbols")
        if symbols is None and isinstance(data.get("data"), list):
            symbols = data["data"]
    else:
        symbols = data
    return list_market_symbols(symbols)


def read_market(symbol):
    # Use the exact exchange-native symbol from Tabdeal exchangeInfo.
    book = get_data("/depth", {"symbol": symbol, "limit": 5})
    bids, asks = parse_order_book(book)
    best_bid, best_ask = bids[0][0], asks[0][0]
    midpoint = best_bid / 2.0 + best_ask / 2.0
    spread = best_ask - best_bid
    return best_bid, best_ask, midpoint, spread


def main():
    print("Tabdeal Futures Market Monitor Started")
    print("Read-only: no real or paper orders are generated.")
    print("Price is the order-book midpoint estimate, not a last-trade price.")
    get_data("/ping")
    symbols = get_symbols()
    print("Active Tabdeal USDT markets:", len(symbols))

    while True:
        print("\n================================")
        print("Scan:", datetime.now().strftime("%H:%M:%S"))
        successful = 0
        errors = 0
        for symbol in symbols:
            try:
                bid, ask, midpoint, spread = read_market(symbol)
                successful += 1
                print(
                    symbol,
                    "Bid:", bid,
                    "Ask:", ask,
                    "Midpoint estimate:", midpoint,
                    "Spread:", spread,
                )
            except Exception as exc:
                errors += 1
                print(symbol, "ERROR:", str(exc)[:180])
            time.sleep(0.15)
        print("Scan completed. Successful:", successful, "Errors:", errors)
        time.sleep(60)


if __name__ == "__main__":
    main()
