"""Safe single-snapshot reader for the Tabdeal Futures order book.

The caller owns scheduling and must prevent overlapping requests. This module
performs exactly one read-only GET and never returns stale data as fresh data.
"""
import datetime
import re

from orderbook import parse_order_book


_SYMBOL_RE = re.compile(r"^[A-Z0-9]+_[A-Z0-9]+$")


def fetch_order_book_snapshot(session, base_url, symbol, verify_path,
                              timeout=(3.05, 6.0)):
    """Fetch and validate one current depth snapshot for a verified symbol."""
    symbol = str(symbol or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("invalid futures symbol")
    url = base_url.rstrip("/") + "/depth"
    response = session.get(
        url,
        params={"symbol": symbol, "limit": 5},
        timeout=timeout,
        verify=verify_path,
        headers={"Accept": "application/json", "Connection": "close"},
    )
    try:
        status = int(response.status_code)
        if status != 200:
            detail = str(getattr(response, "text", ""))[:240].replace("\n", " ")
            raise RuntimeError("depth HTTP {}: {}".format(status, detail))
        try:
            payload = response.json()
        except Exception as exc:
            raise ValueError("depth response is not valid JSON: {}".format(
                type(exc).__name__
            ))
        bids, asks = parse_order_book(payload)
        best_bid, best_ask = bids[0][0], asks[0][0]
        midpoint = best_bid / 2.0 + best_ask / 2.0
        if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
            raise ValueError("invalid best bid/ask")
        return {
            "symbol": symbol,
            "bids": bids[:5],
            "asks": asks[:5],
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": midpoint,
            "depth_status": status,
            "received_at": datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "market_price_source": "order_book_midpoint_estimate",
        }
    finally:
        response.close()
