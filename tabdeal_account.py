"""Session-only, read-only Tabdeal USDT-M futures account client.

This module deliberately exposes GET requests only. It never stores credentials
and never sends or cancels orders. Signed account GETs use Tabdeal's read
gateway (/r/fapi/), matching the official Tabdeal Python SDK.
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

READ_BASE_URL = "https://api1.tabdeal.org/r/fapi"


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


def _finite_decimal(value, field):
    """Parse a finite numeric API field; never silently turn malformed data into zero."""
    from decimal import Decimal, InvalidOperation

    if isinstance(value, bool) or value is None:
        raise RuntimeError("Tabdeal account response has invalid " + field)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError("Tabdeal account response has invalid " + field)
    if not number.is_finite():
        raise RuntimeError("Tabdeal account response has non-finite " + field)
    return number


def read_futures_account(key, secret, timeout=20, session=None):
    """Return validated wallet summary and non-zero futures positions using GET only."""
    account = _signed_get(
        "/v3/account", key, secret, timeout=timeout, session=session
    )
    positions = _signed_get(
        "/v3/positionRisk", key, secret, timeout=timeout, session=session
    )
    if not isinstance(account, dict):
        raise RuntimeError("Unexpected account response format")
    # Some API gateways wrap the account object. Unwrap only an explicit,
    # recognized object container; never infer balances or coerce unknown data.
    for wrapper in ("data", "account", "result"):
        nested = account.get(wrapper)
        if isinstance(nested, dict) and (
            "totalWalletBalance" in nested or "availableBalance" in nested
        ):
            account = nested
            break
    for field in ("totalWalletBalance", "availableBalance"):
        if field not in account:
            raise RuntimeError("Account response is missing " + field + "; received fields: " + ",".join(sorted(str(k) for k in account.keys())[:20]))
        _finite_decimal(account[field], field)
    unrealized = account.get("totalUnrealizedProfit", "0")
    _finite_decimal(unrealized, "totalUnrealizedProfit")

    if isinstance(positions, dict):
        if "positions" in positions:
            positions = positions["positions"]
        elif "data" in positions:
            positions = positions["data"]
            if isinstance(positions, dict) and "positions" in positions:
                positions = positions["positions"]
        else:
            raise RuntimeError("Unexpected positions response wrapper")
    if not isinstance(positions, list):
        raise RuntimeError("Unexpected positions response format")

    active = []
    for index, item in enumerate(positions):
        if not isinstance(item, dict):
            raise RuntimeError("Invalid position row {}".format(index))
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise RuntimeError("Position row {} has no valid symbol".format(index))
        if "positionAmt" in item:
            raw_amount = item["positionAmt"]
        elif "amount" in item:
            raw_amount = item["amount"]
        else:
            raise RuntimeError("Position row {} has no amount".format(index))
        amount = _finite_decimal(raw_amount, "position amount")
        if amount != 0:
            pnl = item.get("unRealizedProfit", item.get("unrealizedProfit", "0"))
            _finite_decimal(pnl, "position unrealized PnL")
            entry_price = item.get("entryPrice", "--")
            mark_price = item.get("markPrice", "--")
            if entry_price != "--":
                _finite_decimal(entry_price, "position entry price")
            if mark_price != "--":
                _finite_decimal(mark_price, "position mark price")
            active.append({
                "symbol": symbol,
                "amount": format(amount, "f"),
                "entry_price": entry_price,
                "mark_price": mark_price,
                "unrealized_pnl": pnl,
                "leverage": item.get("leverage", "--"),
            })
    return {
        "wallet_balance": account["totalWalletBalance"],
        "available_balance": account["availableBalance"],
        "unrealized_pnl": unrealized,
        "positions": active,
    }
