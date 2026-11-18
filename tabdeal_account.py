"""Session-only, read-only Tabdeal USDT-M futures account client.

This module deliberately exposes GET requests only. It never stores credentials
and never sends or cancels orders. Signed account GETs use Tabdeal's read
gateway (/r/fapi/), matching the official Tabdeal Python SDK.
"""
import hashlib
import hmac
import re
import time
from urllib.parse import urlencode

import requests

READ_BASE_URL = "https://api1.tabdeal.org/r/fapi"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+_USDT$")


def _validate_symbol(symbol):
    if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("symbol must be an exchange-native USDT futures symbol")
    return symbol


def _signed_get(path, key, secret, params=None, timeout=20, session=None):
    if not key or not secret:
        raise ValueError("API key and secret are required")
    if not isinstance(path, str) or not path.startswith("/v") or ".." in path:
        raise ValueError("path must be a versioned futures read endpoint")
    values = dict(params or {})
    values["timestamp"] = int(time.time() * 1000)
    query = urlencode(values)
    signature = hmac.new(
        secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    url = READ_BASE_URL + path + "?" + query + "&signature=" + signature
    client = session or requests
    response = client.get(
        url, headers={"X-MBX-APIKEY": key, "Accept": "application/json"},
        timeout=timeout,
    )
    try:
        if not response.ok:
            detail = response.text[:300].replace("\\n", " ")
            raise RuntimeError("Tabdeal HTTP {}: {}".format(response.status_code, detail))
        data = response.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0, "0"):
            raise RuntimeError("Tabdeal API error: {}".format(str(data)[:240]))
        return data
    finally:
        response.close()


def read_futures_account(key, secret, timeout=20, session=None):
    """Return wallet summary and non-zero futures positions using GET only."""
    account = _signed_get(
        "/v3/account", key, secret, timeout=timeout, session=session
    )
    positions = _signed_get(
        "/v3/positionRisk", key, secret, timeout=timeout, session=session
    )
    if not isinstance(account, dict):
        raise RuntimeError("Unexpected account response format")
    if isinstance(positions, dict):
        positions = positions.get("positions", positions.get("data", []))
    if not isinstance(positions, list):
        raise RuntimeError("Unexpected positions response format")
    active = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        try:
            amount = float(item.get("positionAmt", item.get("amount", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if amount:
            position = {
                "symbol": str(item.get("symbol", "--")),
                "amount": amount,
                "entry_price": item.get("entryPrice", "--"),
                "mark_price": item.get("markPrice", "--"),
                "unrealized_pnl": item.get("unRealizedProfit",
                                           item.get("unrealizedProfit", "0")),
                "leverage": item.get("leverage", "--"),
            }
            raw_position_id = item.get("positionId", item.get("position_id"))
            try:
                position_id = int(raw_position_id)
                if position_id > 0:
                    position["position_id"] = position_id
            except (TypeError, ValueError, OverflowError):
                pass
            active.append(position)
    return {
        "wallet_balance": account.get("totalWalletBalance", "--"),
        "available_balance": account.get("availableBalance", "--"),
        "unrealized_pnl": account.get("totalUnrealizedProfit", "0"),
        "positions": active,
        "verified": True,
        "source": "authenticated_futures_account",
        "observed_at": time.time(),
    }


def read_order_status(key, secret, symbol, order_id, timeout=20, session=None):
    """Read one futures order by exchange order ID; this function is GET-only."""
    symbol = _validate_symbol(symbol)
    if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id <= 0:
        raise ValueError("order_id must be a positive integer")
    data = _signed_get(
        "/v1/order", key, secret,
        params={"symbol": symbol, "orderId": order_id},
        timeout=timeout, session=session,
    )
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected order status response format")
    return data


def read_open_orders(key, secret, symbol=None, limit=None, timeout=20, session=None):
    """Read open futures orders; never submits, cancels, or modifies an order."""
    params = {}
    if symbol is not None:
        params["symbol"] = _validate_symbol(symbol)
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        params["limit"] = limit
    data = _signed_get(
        "/v1/openOrders", key, secret, params=params,
        timeout=timeout, session=session,
    )
    if isinstance(data, dict):
        data = data.get("orders", data.get("data", data))
    if not isinstance(data, list):
        raise RuntimeError("Unexpected open orders response format")
    if not all(isinstance(item, dict) for item in data):
        raise RuntimeError("Open orders response contains invalid entries")
    return data
