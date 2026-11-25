"""Read-only Tabdeal futures candle-history warm-up.

The provider requests public one-minute klines only. Returned rows are passed
through the same strict closed/contiguous validator used by the local cache.
No credentials, account endpoints, or order actions are used here.
"""
import re

from candle_history import validate_closed_candles


def fetch_tabdeal_closed_candles(session, api_base, symbol, now_ms,
                                  verify_path, interval_ms=60_000,
                                  limit=100, timeout=(3.05, 8.0)):
    """Fetch and validate recent closed OHLCV candles from the public API.

    Raises on unsupported endpoints, HTTP errors, malformed payloads, or an
    insufficient contiguous history. Callers must keep paper entry disabled
    when this function fails.
    """
    symbol = str(symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
        raise ValueError("invalid futures symbol")
    if interval_ms != 60_000:
        raise ValueError("only one-minute history is supported")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 23 <= limit <= 500:
        raise ValueError("history limit must be between 23 and 500")

    response = session.get(
        api_base.rstrip("/") + "/klines",
        params={"symbol": symbol, "interval": "1m", "limit": limit},
        timeout=timeout,
        verify=verify_path,
        headers={"Accept": "application/json", "Connection": "close"},
    )
    try:
        if int(response.status_code) != 200:
            detail = str(getattr(response, "text", ""))[:180].replace("\\n", " ")
            raise RuntimeError("history HTTP {}: {}".format(
                response.status_code, detail
            ))
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("history response must be a list")
        # The validator consumes the first six standard OHLCV fields and
        # excludes the currently-forming minute by its open timestamp.
        rows = validate_closed_candles(
            payload, int(now_ms), interval_ms=interval_ms, limit=limit
        )
        if len(rows) < 23:
            raise ValueError(
                "insufficient contiguous closed candles: {} of 23".format(len(rows))
            )
        return rows
    finally:
        response.close()
