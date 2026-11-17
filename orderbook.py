"""Strictly normalize Tabdeal Futures depth payloads into price/quantity levels."""
import json
import math

_ENVELOPES = ("data", "result", "payload", "orderBook", "orderbook", "depth")
_BIDS = ("bids", "b", "buy", "buys")
_ASKS = ("asks", "a", "sell", "sells")


def _field(mapping, names):
    lowered = {str(key).lower(): key for key in mapping}
    for name in names:
        key = lowered.get(name.lower())
        if key is not None and mapping[key] is not None:
            return mapping[key]
    return None


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("missing numeric value")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("empty numeric value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite numeric value")
    return number


def _levels(raw):
    if isinstance(raw, dict):
        if _field(raw, ("price", "p", "bidPrice", "askPrice")) is not None:
            raw = [raw]
        else:
            raw = list(raw.items())
    if not isinstance(raw, (list, tuple)):
        return []
    totals = {}
    for item in raw:
        try:
            if isinstance(item, dict):
                price = _field(item, ("price", "p", "bidPrice", "askPrice"))
                quantity = _field(item, ("qty", "quantity", "amount", "q", "size", "volume", "baseQty"))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, quantity = item[0], item[1]
            else:
                continue
            price, quantity = _number(price), _number(quantity)
            if price > 0 and quantity > 0:
                combined = totals.get(price, 0.0) + quantity
                if not math.isfinite(combined):
                    continue
                totals[price] = combined
        except (TypeError, ValueError, OverflowError):
            continue
    return list(totals.items())


def _unwrap(payload):
    current = payload
    for _ in range(8):
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except (TypeError, ValueError):
                break
        if not isinstance(current, dict):
            break
        bids, asks = _field(current, _BIDS), _field(current, _ASKS)
        if bids is not None or asks is not None:
            # Some gateways include empty outer arrays while the actual book
            # is nested in data/result. Prefer a usable outer book; otherwise
            # continue into the wrapper before reporting an empty snapshot.
            if _levels(bids) and _levels(asks):
                return current
            nested = _field(current, _ENVELOPES)
            if nested is None or nested is current:
                return current
            current = nested
            continue
        code = _field(current, ("code", "errorCode"))
        message = _field(current, ("msg", "message", "error"))
        if code not in (None, 0, "0", "OK", "ok", 200, "200"):
            raise ValueError("depth API error {}: {}".format(code, message or "no message"))
        nested = _field(current, _ENVELOPES)
        if nested is None or nested is current:
            break
        current = nested
    return current


def parse_order_book(payload):
    """Return sorted (bids, asks); reject empty, malformed and crossed books."""
    current = _unwrap(payload)
    if not isinstance(current, dict):
        raise ValueError("depth response is not an order-book object")
    bids = _levels(_field(current, _BIDS) or [])
    asks = _levels(_field(current, _ASKS) or [])
    bids.sort(key=lambda item: item[0], reverse=True)
    asks.sort(key=lambda item: item[0])
    if not bids or not asks:
        raise ValueError(
            "depth response has no usable bids/asks (bids={}, asks={}, keys={})".format(
                len(bids), len(asks), ", ".join(sorted(str(key) for key in current)) or "none"
            )
        )
    if asks[0][0] < bids[0][0]:
        raise ValueError("crossed order book: best ask is below best bid")
    return bids, asks
