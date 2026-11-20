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


def _signed_get(path, key, secret, params=None, timeout=20, session=None, timestamp_ms=None):
    if not key or not secret:
        raise ValueError("API key and secret are required")
    if not isinstance(path, str) or not path.startswith("/v") or ".." in path:
        raise ValueError("path must be a versioned futures read endpoint")
    values = dict(params or {})
    values["timestamp"] = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
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


def _server_time_ms(session, timeout=12):
    """Read Tabdeal's public server clock to avoid Android device clock drift."""
    response = session.get(
        READ_BASE_URL + "/v1/time",
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    try:
        if not response.ok:
            raise RuntimeError("Tabdeal time endpoint HTTP {}".format(response.status_code))
        payload = response.json()
        if isinstance(payload, dict):
            value = payload.get("serverTime", payload.get("server_time", payload.get("time")))
        else:
            value = payload
        server_ms = int(value)
        if server_ms <= 0:
            raise ValueError("invalid server time")
        return server_ms
    except Exception as exc:
        raise RuntimeError("Tabdeal server-time sync failed: {}: {}".format(
            type(exc).__name__, str(exc)[:160]
        )) from exc
    finally:
        response.close()


def read_futures_account(key, secret, timeout=20, session=None):
    """Return validated wallet summary and non-zero futures positions using GET only."""
    own_session = session is None
    client = session or requests.Session()
    try:
        if own_session:
            import certifi
            from android_tls import mount_tabdeal_tls_compat
            mount_tabdeal_tls_compat(client, certifi.where())
        if own_session:
            server_ms = _server_time_ms(client, timeout=min(timeout, 12))
            offset = server_ms - int(time.time() * 1000)
            signed_timestamp = int(time.time() * 1000) + offset
        else:
            signed_timestamp = None
        signed_params = {"recvWindow": 10000}
        account = _signed_get(
            "/v3/account", key, secret, params=signed_params,
            timeout=timeout, session=client, timestamp_ms=signed_timestamp
        )
        positions = _signed_get(
            "/v3/positionRisk", key, secret, params=signed_params,
            timeout=timeout, session=client,
            timestamp_ms=(signed_timestamp + 1 if signed_timestamp is not None else None)
        )
    finally:
        if own_session:
            client.close()
    if not isinstance(account, dict):
        raise RuntimeError("Unexpected account response format")
    # Some API gateways wrap the account object. Accept only known account
    # fields, including an assets-only response; do not unwrap arbitrary data.
    for wrapper in ("data", "account", "result"):
        nested = account.get(wrapper)
        if isinstance(nested, dict) and any(
            field in nested for field in (
                "totalWalletBalance", "availableBalance", "assets"
            )
        ):
            account = nested
            break

    # Tabdeal may return the Futures account with assets[] but without the
    # aggregate totalWalletBalance/availableBalance fields. This app is for
    # USDT-M Futures, so use the USDT asset only; never add BTC/other assets
    # together or substitute wallet balance for available balance.
    usdt_asset = None
    assets = account.get("assets")
    if isinstance(assets, list):
        usdt_rows = [
            row for row in assets
            if isinstance(row, dict)
            and str(row.get("asset", "")).strip().upper() == "USDT"
        ]
        if len(usdt_rows) > 1:
            raise RuntimeError("Account response contains duplicate USDT asset rows")
        if usdt_rows:
            usdt_asset = usdt_rows[0]

    wallet_balance = account.get("totalWalletBalance")
    if wallet_balance is None and usdt_asset is not None:
        wallet_balance = usdt_asset.get("walletBalance")
    available_balance = account.get("availableBalance")
    if available_balance is None and usdt_asset is not None:
        available_balance = usdt_asset.get("availableBalance")
    unrealized = account.get("totalUnrealizedProfit")
    if unrealized is None:
        unrealized = usdt_asset.get("unrealizedProfit", "0") if usdt_asset else "0"

    if wallet_balance is None:
        raise RuntimeError(
            "Account response is missing totalWalletBalance or assets[].walletBalance "
            "for USDT; received fields: "
            + ",".join(sorted(str(k) for k in account.keys())[:20])
        )
    if available_balance is None:
        raise RuntimeError(
            "Account response is missing availableBalance for USDT; received fields: "
            + ",".join(sorted(str(k) for k in account.keys())[:20])
        )
    _finite_decimal(wallet_balance, "totalWalletBalance")
    _finite_decimal(available_balance, "availableBalance")
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
        "wallet_balance": wallet_balance,
        "available_balance": available_balance,
        "unrealized_pnl": unrealized,
        "positions": active,
    }
