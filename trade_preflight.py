"""Fail-closed preflight validation for a future live-order coordinator.

This module is deliberately pure: it never reads credentials, opens a network
connection, or submits an order. It validates exchange filters and account
position state before any execution layer may consider an order.
"""
import math
from decimal import Decimal, InvalidOperation


def _decimal(value, name):
    if isinstance(value, bool) or value is None:
        raise ValueError("{} must be a finite positive number".format(name))
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("{} must be a finite positive number".format(name))
    if not result.is_finite() or result <= 0:
        raise ValueError("{} must be a finite positive number".format(name))
    return result


def _filters(symbol_info):
    raw = symbol_info.get("filters")
    if not isinstance(raw, list):
        raise ValueError("exchange filters are missing")
    result = {}
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("filterType"), str):
            result[item["filterType"]] = item
    return result


def validate_order_preflight(symbol, side, quantity, reference_price,
                             symbol_info, open_positions=(),
                             max_open_positions=1, max_notional_usdt=None):
    """Return a normalized order plan or raise ValueError; never place orders."""
    if not isinstance(symbol, str) or not symbol.endswith("_USDT"):
        raise ValueError("invalid exchange-native USDT symbol")
    if not isinstance(symbol_info, dict) or symbol_info.get("symbol") != symbol:
        raise ValueError("exchange symbol metadata does not match")
    if symbol_info.get("status") not in ("TRADING", "ENABLED"):
        raise ValueError("symbol is not enabled for trading")
    if symbol_info.get("quoteAsset") not in (None, "USDT"):
        raise ValueError("symbol quote asset is not USDT")
    if symbol_info.get("contractType") not in (None, "PERPETUAL"):
        raise ValueError("only perpetual contracts are supported")
    side = str(side).upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")

    qty = _decimal(quantity, "quantity")
    price = _decimal(reference_price, "reference_price")
    filters = _filters(symbol_info)
    lot = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE")
    if not isinstance(lot, dict):
        raise ValueError("quantity filter is missing")
    step = _decimal(lot.get("stepSize"), "stepSize")
    minimum = _decimal(lot.get("minQty"), "minQty")
    maximum = _decimal(lot.get("maxQty"), "maxQty")
    if qty < minimum or qty > maximum:
        raise ValueError("quantity is outside exchange limits")
    if qty % step != 0:
        raise ValueError("quantity does not match exchange step size")

    price_filter = filters.get("PRICE_FILTER")
    if price_filter:
        tick = _decimal(price_filter.get("tickSize"), "tickSize")
        if price % tick != 0:
            raise ValueError("reference price does not match exchange tick size")

    notional = qty * price
    notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")
    if notional_filter:
        min_notional = _decimal(
            notional_filter.get("minNotional", notional_filter.get("notional")),
            "minNotional")
        if notional < min_notional:
            raise ValueError("order notional is below exchange minimum")
    if max_notional_usdt is not None:
        cap = _decimal(max_notional_usdt, "max_notional_usdt")
        if notional > cap:
            raise ValueError("order exceeds configured notional cap")

    if isinstance(max_open_positions, bool) or not isinstance(max_open_positions, int) or max_open_positions < 1:
        raise ValueError("max_open_positions must be a positive integer")
    if not isinstance(open_positions, (list, tuple)):
        raise ValueError("open position snapshot is invalid")
    active = []
    for position in open_positions:
        if not isinstance(position, dict):
            raise ValueError("open position snapshot contains invalid data")
        position_symbol = position.get("symbol")
        if not isinstance(position_symbol, str) or not position_symbol.strip():
            raise ValueError("open position symbol is missing or invalid")
        if "positionAmt" in position:
            amount = position["positionAmt"]
        elif "amount" in position:
            amount = position["amount"]
        else:
            raise ValueError("open position amount is missing")
        try:
            amount_value = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("open position amount is invalid")
        if not amount_value.is_finite():
            raise ValueError("open position amount is invalid")
        if amount_value != 0:
            active.append(position)
    # Prefer the specific duplicate-symbol error before the global cap.
    if any(p.get("symbol") == symbol for p in active):
        raise ValueError("a position for this symbol is already open")
    if len(active) >= max_open_positions:
        raise ValueError("open position limit reached")

    return {
        "symbol": symbol,
        "side": side,
        "quantity": format(qty, "f"),
        "reference_price": format(price, "f"),
        "notional_usdt": format(notional, "f"),
        "preflight_only": True,
        "live_order_submitted": False,
    }
