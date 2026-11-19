"""Fail-closed Tabdeal USDT-M futures trading adapter.

Endpoints and parameter names follow the official Tabdeal Python SDK.
Live methods are disabled by default and require both a client opt-in and a
per-call confirmation. No credentials are persisted by this module.
"""
import hashlib
import hmac
import math
import re
from decimal import Decimal, InvalidOperation
import time
from urllib.parse import urlencode

import requests

TRADE_BASE_URL = "https://api1.tabdeal.org/fapi"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+_USDT$")


def _positive(value, name):
    """Parse a positive finite financial value without binary-float rounding."""
    if isinstance(value, bool):
        raise ValueError("{} must be positive and finite".format(name))
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("{} must be positive and finite".format(name))
    if not number.is_finite() or number <= 0:
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
        if not self.base_url.endswith("/fapi"):
            raise ValueError("base_url must end with /fapi")
        self.read_base_url = self.base_url[:-5] + "/r/fapi"

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
        """Perform authenticated read-only requests only.

        Mutating HTTP verbs are deliberately unavailable through this generic
        transport. This prevents callers from bypassing the public trading
        methods' safety gates by invoking the transport directly.
        """
        allowed_reads = {
            "/v1/position", "/v1/order", "/v1/openOrders",
            "/v3/positionRisk",
        }
        if method != "GET" or path not in allowed_reads:
            raise ValueError("unsupported or mutating HTTP method/endpoint")
        values = dict(params)
        values["timestamp"] = int(time.time() * 1000)
        query = urlencode(values)
        values["signature"] = hmac.new(
            self._api_secret.encode("utf-8"), query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response = None
        try:
            response = self.session.get(
                self.read_base_url + path, params=values,
                headers={"X-MBX-APIKEY": self._api_key,
                         "Accept": "application/json"},
                timeout=self.timeout,
            )
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

    def get_positions(self, symbol=None):
        """Read futures positions through the SDK's authenticated read gateway.

        The returned exchange payload is intentionally not normalized: callers
        must inspect and validate its positionId/state fields before any action.
        """
        params = {}
        if symbol is not None:
            params["symbol"] = self._validate_symbol(symbol)
        return self._signed_request("GET", "/v1/position", params)

    def get_open_orders(self, symbol=None):
        """Read currently open futures orders; this method never changes orders."""
        params = {}
        if symbol is not None:
            params["symbol"] = self._validate_symbol(symbol)
        return self._signed_request("GET", "/v1/openOrders", params)

    def get_order(self, symbol, order_id):
        """Read one futures order by its exchange order ID."""
        symbol = self._validate_symbol(symbol)
        if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id <= 0:
            raise ValueError("order_id must be a positive integer")
        return self._signed_request("GET", "/v1/order", {
            "symbol": symbol,
            "orderId": order_id,
        })

    def get_position_risk(self, symbol=None):
        """Read futures position-risk details through the documented v3 endpoint."""
        params = {}
        if symbol is not None:
            params["symbol"] = self._validate_symbol(symbol)
        return self._signed_request("GET", "/v3/positionRisk", params)

    def place_market_order(self, symbol, side, quantity, reduce_only=False,
                           confirm_live=False):
        """Validate inputs, then fail closed for every unverified MARKET write."""
        self._require_live_confirmation(confirm_live)
        symbol = self._validate_symbol(symbol)
        side = str(side).upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        _positive(quantity, "quantity")
        if not isinstance(reduce_only, bool):
            raise ValueError("reduce_only must be boolean")
        if not reduce_only:
            raise RuntimeError(
                "Risk-increasing market entries are blocked until protective "
                "stop-loss/take-profit placement can be verified"
            )
        raise RuntimeError(
            "All MARKET orders are disabled until reduceOnly semantics and "
            "position-side behavior are verified against Tabdeal"
        )

    def set_position_sl_tp(self, symbol, position_id, sl_price=None,
                           tp_price=None, position_side=None,
                           reference_price=None, confirm_live=False):
        """Validate requested levels, then fail closed until protection is verifiable.

        The current documented read responses do not establish an independently
        verifiable link between both active SL/TP triggers and this position.
        Caller-provided IDs, direction, and reference prices are not sufficient
        evidence. Do not treat an API acknowledgement as proof of protection.
        """
        self._require_live_confirmation(confirm_live)
        self._validate_symbol(symbol)
        if isinstance(position_id, bool) or not isinstance(position_id, int) or position_id <= 0:
            raise ValueError("position_id must be a positive integer")
        if sl_price is None or tp_price is None:
            raise ValueError("both sl_price and tp_price are required")
        if position_side not in ("LONG", "SHORT"):
            raise ValueError("position_side must be LONG or SHORT")
        reference = _positive(reference_price, "reference_price")
        sl = _positive(sl_price, "sl_price")
        tp = _positive(tp_price, "tp_price")
        if position_side == "LONG" and not (sl < reference < tp):
            raise ValueError("LONG requires sl_price < reference_price < tp_price")
        if position_side == "SHORT" and not (tp < reference < sl):
            raise ValueError("SHORT requires tp_price < reference_price < sl_price")
        raise RuntimeError(
            "SL/TP writes are disabled until the live position, trigger basis, "
            "and both active protective orders can be independently verified"
        )

    def close_position(self, symbol, confirm_live=False):
        """Disabled: Tabdeal documents DELETE /v1/position as closing ALL
        open positions for the supplied symbol, not one positionId.

        This broad symbol-level scope can close multiple positions at once.
        Keep this operation fail-closed until a separately reviewed workflow
        can prove that closing every position for that symbol is intended.
        """
        raise RuntimeError(
            "Position closing is disabled: Tabdeal closes all open positions for the symbol"
        )
