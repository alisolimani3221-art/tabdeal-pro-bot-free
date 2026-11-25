"""Bounded, read-only order-book scan across active Tabdeal USDT futures markets.

This module reports liquidity snapshots only. It never derives candles/signals
from order-book prices and never submits exchange orders.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import requests
import certifi
from android_tls import mount_tabdeal_tls_compat

from market_symbols import list_market_symbols
from orderbook import parse_order_book

FUTURES_API = "https://api1.tabdeal.org/r/fapi/v1"


def extract_active_symbols(payload):
    """Extract the active USDT perpetual symbol list from exchangeInfo."""
    if isinstance(payload, dict):
        symbols = payload.get("symbols")
        if symbols is None:
            nested = payload.get("data")
            if isinstance(nested, dict):
                symbols = nested.get("symbols")
            elif isinstance(nested, list):
                symbols = nested
    else:
        symbols = payload
    return list_market_symbols(symbols)


def load_active_market_symbols(fetch_json=None):
    """Return every active USDT perpetual contract from exchangeInfo."""
    fetch = fetch_json or _request_json
    symbols = extract_active_symbols(fetch("/exchangeInfo", None))
    if not symbols:
        raise RuntimeError("No active USDT futures symbols were returned")
    return symbols


def _request_json(path, params=None, timeout=18):
    with requests.Session() as session:
        mount_tabdeal_tls_compat(session, certifi.where())
        session.headers.update({
            "User-Agent": "FuturesAnalyzer-ReadOnly-Scanner/1.0",
            "Accept": "application/json",
            "Connection": "close",
        })
        response = session.get(
            FUTURES_API + path, params=params, timeout=timeout,
            verify=certifi.where(),
        )
        try:
            response.raise_for_status()
            return response.json()
        finally:
            response.close()


def scan_market_universe(fetch_json=None, max_workers=6):
    """Read all active order books with bounded concurrency; return snapshots/errors.

    fetch_json is injectable for deterministic tests and must accept (path,
    params). The scanner intentionally does not calculate technical signals.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be an integer between 1 and 8")
    fetch = fetch_json or _request_json
    payload = fetch("/exchangeInfo", None)
    symbols = extract_active_symbols(payload)
    if not symbols:
        raise RuntimeError("No active USDT futures symbols were returned")

    def read_one(symbol):
        # Retry transient network/HTTP failures, but never retry malformed
        # market data: a bad book must remain visible as a failed symbol.
        for attempt in range(3):
            try:
                book = fetch("/depth", {"symbol": symbol, "limit": 5})
                break
            except Exception as exc:
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                if status is None:
                    status = getattr(exc, "status_code", None)
                retryable = status is None or status == 429 or (
                    isinstance(status, int) and status >= 500
                )
                if not retryable or attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
        bids, asks = parse_order_book(book)
        bid, ask = float(bids[0][0]), float(asks[0][0])
        midpoint = bid / 2.0 + ask / 2.0
        spread = ask - bid
        spread_pct = spread / midpoint * 100.0 if midpoint > 0 else float("inf")
        return {
            "symbol": symbol,
            "best_bid": bid,
            "best_ask": ask,
            "midpoint_estimate": midpoint,
            "spread": spread,
            "spread_pct": spread_pct,
            "bid_levels": len(bids),
            "ask_levels": len(asks),
        }

    snapshots, errors = [], []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(read_one, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                snapshots.append(future.result())
            except Exception as exc:
                errors.append({
                    "symbol": symbol,
                    "error": "{}: {}".format(type(exc).__name__, str(exc)[:160]),
                })
    snapshots.sort(key=lambda item: (item["spread_pct"], item["symbol"]))
    errors.sort(key=lambda item: item["symbol"])
    return {
        "active_count": len(symbols),
        "success_count": len(snapshots),
        "error_count": len(errors),
        "snapshots": snapshots,
        "errors": errors,
        "data_type": "order_book_snapshot_only",
        "technical_signals": False,
        "live_orders": False,
    }
