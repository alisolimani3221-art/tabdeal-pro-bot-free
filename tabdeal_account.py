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


def _unwrap_account_payload(payload):
    """Unwrap common API envelopes without assuming a single response shape."""
    current = payload
    for _ in range(4):
        if not isinstance(current, dict):
            break
        nested = None
        for name in ("data", "result", "account", "accountData", "account_data"):
            value = current.get(name)
            if isinstance(value, dict):
                nested = value
                break
        if nested is None:
            break
        current = nested
    if not isinstance(current, dict):
        raise RuntimeError("Unexpected account response format")
    return current


def _first_present(mapping, names, default=None):
    for name in names:
        if name in mapping and mapping[name] is not None and mapping[name] != "":
            return mapping[name]
    return default


def read_futures_account(key, secret, timeout=20, session=None):
    """Return wallet summary and non-zero futures positions using GET only.

    Tabdeal deployments may wrap the account payload in data/result/account.
    Accept known wallet-field spellings, but fail with a diagnostic if no
    wallet balance is present rather than showing a misleading zero.
    """
    raw_account = _signed_get(
        "/v3/account", key, secret, timeout=timeout, session=session
    )
    account = _unwrap_account_payload(raw_account)
    wallet = _first_present(account, (
        "totalWalletBalance", "total_wallet_balance", "walletBalance",
        "wallet_balance", "totalBalance", "total_balance",
    ))
    if wallet is None:
        keys = ", ".join(sorted(str(k) for k in account.keys())[:30])
        raise RuntimeError(
            "Account response has no recognized wallet balance field; fields: "
            + (keys or "(empty)")
        )

    available = _first_present(account, (
        "availableBalance", "available_balance", "availableWalletBalance",
        "available_wallet_balance", "maxWithdrawAmount",
    ), default="--")
    pnl = _first_present(account, (
        "totalUnrealizedProfit", "total_unrealized_profit",
        "unrealizedProfit", "unrealized_pnl",
    ), default="0")

    raw_positions = _signed_get(
        "/v3/positionRisk", key, secret, timeout=timeout, session=session
    )
    positions = raw_positions
    if isinstance(positions, dict):
        for name in ("positions", "data", "result"):
            if isinstance(positions.get(name), list):
                positions = positions[name]
                break
        else:
            positions = []
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
                "entry_price": item.get("entryPrice", item.get("entry_price", "--")),
                "mark_price": item.get("markPrice", item.get("mark_price", "--")),
                "unrealized_pnl": item.get(
                    "unRealizedProfit",
                    item.get("unrealizedProfit", item.get("unrealized_pnl", "0"))
                ),
                "leverage": item.get("leverage", "--"),
            })
    return {
        "wallet_balance": wallet,
        "available_balance": available,
        "unrealized_pnl": pnl,
        "positions": active,
    }
