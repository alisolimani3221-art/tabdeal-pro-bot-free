"""Read-only Tabdeal Futures order-book monitor for a Linux VPS.

This service uses only documented public REST endpoints and never submits
orders. It does not invent candles or trading signals from an order-book quote.
"""
import json
import logging
import os
import signal
import time
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from market_symbols import list_market_symbols
from orderbook import parse_order_book

def load_config(environ=None):
    """Validate monitor configuration before starting network activity."""
    values = os.environ if environ is None else environ
    api = values.get("TABDEAL_FUTURES_API", "https://api1.tabdeal.org/r/fapi/v1").strip()
    parsed = urlsplit(api)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("TABDEAL_FUTURES_API must be an HTTPS base URL without credentials, query or fragment")
    if any(part == ".." for part in parsed.path.split("/")):
        raise ValueError("TABDEAL_FUTURES_API path must not contain parent segments")

    def read_integer(name, default):
        raw = values.get(name, default)
        try:
            return int(raw)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("{} must be an integer".format(name))

    interval = max(60, read_integer("PAPER_SCAN_SECONDS", "300"))
    workers = max(1, min(12, read_integer("PAPER_WORKERS", "8")))
    return api.rstrip("/"), interval, workers


API, INTERVAL, WORKERS = load_config()
STOP = False

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tabdeal-market-monitor")


def request_json(session, path, params=None, timeout=20):
    response = session.get(API + path, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def read_market(symbol):
    with requests.Session() as session:
        session.headers.update({
            "User-Agent": "Tabdeal-Futures-Monitor/1.0",
            "Accept": "application/json",
        })
        book = request_json(session, "/depth", {"symbol": symbol, "limit": 5})
    bids, asks = parse_order_book(book)
    bid, ask = bids[0][0], asks[0][0]
    return {
        "symbol": symbol,
        "best_bid": bid,
        "best_ask": ask,
        "midpoint_estimate": bid / 2.0 + ask / 2.0,
        "spread": ask - bid,
    }


def scan():
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Tabdeal-Futures-Monitor/1.0"})
        request_json(session, "/ping")
        info = request_json(session, "/exchangeInfo", timeout=25)

    if isinstance(info, dict):
        symbols_data = info.get("symbols")
        if symbols_data is None:
            nested = info.get("data", {})
            symbols_data = nested.get("symbols") if isinstance(nested, dict) else nested
    else:
        symbols_data = info
    symbols = list_market_symbols(symbols_data)
    if not symbols:
        raise RuntimeError("No active USDT perpetual contracts returned by exchangeInfo")

    snapshots, failures = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(read_market, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            try:
                snapshots.append(future.result())
            except Exception as exc:
                failures.append((futures[future], str(exc)[:180]))

    snapshots.sort(key=lambda item: item["symbol"])
    for item in snapshots:
        log.info(
            "%s bid=%s ask=%s midpoint_estimate=%s spread=%s",
            item["symbol"], item["best_bid"], item["best_ask"],
            item["midpoint_estimate"], item["spread"],
        )
    log.info(
        "Read-only scan complete: active=%d orderbooks_ok=%d errors=%d. "
        "No candles, technical signals, paper entries or live orders were generated.",
        len(symbols), len(snapshots), len(failures),
    )
    if failures:
        log.warning("Sample market-data errors: %s", failures[:5])


def stop_handler(_signum, _frame):
    global STOP
    STOP = True
    log.warning("Shutdown requested; monitor has no exchange orders.")


def main():
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    log.warning(
        "Read-only Tabdeal Futures monitor started. interval=%ds. "
        "Historical candles are not part of the documented public REST API; "
        "automatic trading is disabled.",
        INTERVAL,
    )
    while not STOP:
        started = time.monotonic()
        try:
            scan()
        except Exception:
            log.exception("Market-data scan failed.")
        remaining = max(1, INTERVAL - int(time.monotonic() - started))
        while remaining and not STOP:
            time.sleep(min(1, remaining))
            remaining -= 1
    log.info("Read-only monitor stopped.")


if __name__ == "__main__":
    main()
