"""Session-only, read-only Tabdeal USDT-M futures account client.

This module deliberately exposes GET requests only. It never stores credentials
and never sends or cancels orders.
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

BASE_URL = "https://api1.tabdeal.org"


def _signed_get(path, key, secret, params=None, timeout=20, session=None):
    if not key or not secret:
        raise ValueError("API key and secret are required")
    values = dict(params or {})
    values["timestamp"] = int(time.time() * 1000)
    query = urlencode(values)
    signature = hmac.new(
        secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    url = BASE_URL + path + "?" + query + "&signature=" + signature
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
        "/fapi/v3/account", key, secret, timeout=timeout, session=session
    )
    positions = _signed_get(
        "/fapi/v3/positionRisk", key, secret, timeout=timeout, session=session
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
            active.append({
                "symbol": str(item.get("symbol", "--")),
                "amount": amount,
                "entry_price": item.get("entryPrice", "--"),
                "mark_price": item.get("markPrice", "--"),
                "unrealized_pnl": item.get("unRealizedProfit",
                                           item.get("unrealizedProfit", "0")),
                "leverage": item.get("leverage", "--"),
            })
    return {
        "wallet_balance": account.get("totalWalletBalance", "--"),
        "available_balance": account.get("availableBalance", "--"),
        "unrealized_pnl": account.get("totalUnrealizedProfit", "0"),
        "positions": active,
    }
