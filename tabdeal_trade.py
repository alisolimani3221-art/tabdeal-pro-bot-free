"""Fail-closed Tabdeal USDT-M futures trading adapter.

Endpoints and parameter names follow the official Tabdeal Python SDK.
Live methods are disabled by default and require both a client opt-in and a
per-call confirmation. No credentials are persisted by this module.
"""
import hashlib
import hmac
import math
import re
import time
from urllib.parse import urlencode

import requests

TRADE_BASE_URL = "https://api1.tabdeal.org/fapi"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+_USDT$")


def _positive(value, name):
    if isinstance(value, bool):
        raise ValueError("{} must be positive and finite".format(name))
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("{} must be positive and finite".format(name))
    if not math.isfinite(number) or number <= 0:
        raise ValueError("{} must be positive and finite".format(name))
    return number


class TabdealFuturesTradeClient:
    """Authenticated futures order client; live order methods fail closed."""

    def __init__(self, api_key, api_secret, live_enabled=False,
                 timeout=15, session=None, base_url=TRADE_BASE_URL):
        if not api_key or not api_secret:
            raise ValueError("API key and secret are required")
        if not isinstance(live_enabled, bool):
            raise ValueError("live_enabled must be boolean")
        if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or float(timeout) <= 0:
            raise ValueError("timeout must be positive and finite")
        if not isinstance(base_url, str) or not base_url.startswith("https://"):
            raise ValueError("base_url must use HTTPS")
        self._api_key = str(api_key)
        self._api_secret = str(api_secret)
        self.live_enabled = live_enabled
        self.timeout = float(timeout)
        self.session = session or requests
        self.base_url = base_url.rstrip("/")

    def _require_live_confirmation(self, confirm_live):
        if not self.live_enabled or confirm_live is not True:
            raise RuntimeError(
                "Live trading is disabled; enable it explicitly and confirm this operation"
            )

    @staticmethod
    def _validate_symbol(symbol):
        if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("symbol must be an exchange-native USDT futures symbol")
        return symbol

    def _signed_request(self, method, path, params):
        if path not in ("/v1/order", "/v1/position", "/v1/positionSlTp"):
            raise ValueError("unsupported trading endpoint")
        values = dict(params)
        values["timestamp"] = int(time.time() * 1000)
        query = urlencode(values)
        values["signature"] = hmac.new(
            self._api_secret.encode("utf-8"), query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        url = self.base_url + path
        response = None
        try:
            if method == "POST":
                response = self.session.post(
                    url, data=values,
                    headers={"X-MBX-APIKEY": self._api_key,
                             "Accept": "application/json"},
                    timeout=self.timeout,
                )
            elif method == "DELETE":
                response = self.session.delete(
                    url, params=values,
                    headers={"X-MBX-APIKEY": self._api_key,
                             "Accept": "application/json"},
                    timeout=self.timeout,
                )
            else:
                raise ValueError("unsupported HTTP method")
            if not response.ok:
                detail = str(getattr(response, "text", ""))[:240]
                raise RuntimeError("Tabdeal HTTP {}: {}".format(
                    response.status_code, detail
                ))
            data = response.json()
            if isinstance(data, dict) and data.get("code") not in (None, 0, "0"):
                raise RuntimeError("Tabdeal API error: {}".format(str(data)[:240]))
            return data
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()

    def place_market_order(self, symbol, side, quantity, reduce_only=False,
                           confirm_live=False):
        """Submit a MARKET order only after two explicit live-trading gates."""
        self._require_live_confirmation(confirm_live)
        symbol = self._validate_symbol(symbol)
        side = str(side).upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        qty = _positive(quantity, "quantity")
        if not isinstance(reduce_only, bool):
            raise ValueError("reduce_only must be boolean")
        return self._signed_request("POST", "/v1/order", {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": format(qty, ".16g"),
            "reduceOnly": str(reduce_only).lower(),
        })

    def set_position_sl_tp(self, symbol, position_id, sl_price=None,
                           tp_price=None, confirm_live=False):
        """Set exchange-side stop-loss/take-profit for an existing position."""
        self._require_live_confirmation(confirm_live)
        symbol = self._validate_symbol(symbol)
        if isinstance(position_id, bool) or not isinstance(position_id, int) or position_id <= 0:
            raise ValueError("position_id must be a positive integer")
        params = {"symbol": symbol, "positionId": position_id}
        if sl_price is not None:
            params["slPrice"] = format(_positive(sl_price, "sl_price"), ".16g")
        if tp_price is not None:
            params["tpPrice"] = format(_positive(tp_price, "tp_price"), ".16g")
        if len(params) == 2:
            raise ValueError("at least one of sl_price or tp_price is required")
        return self._signed_request("POST", "/v1/positionSlTp", params)

    def close_position(self, symbol, confirm_live=False):
        """Close the exchange position for a symbol; requires explicit confirmation."""
        self._require_live_confirmation(confirm_live)
        symbol = self._validate_symbol(symbol)
        return self._signed_request("DELETE", "/v1/position", {
            "symbol": symbol,
        })
