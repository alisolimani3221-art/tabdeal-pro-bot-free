"""Normalize Tabdeal futures depth payloads into sorted numeric price/quantity levels."""

import math


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("missing numeric value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite numeric value")
    return number


def _levels(raw):
    if isinstance(raw, dict):
        # A single object level: {"price": "...", "qty": "..."}.
        level_keys = {"price", "p", "qty", "quantity", "amount", "q"}
        if level_keys.intersection(raw):
            raw = [raw]
        else:
            # Some providers encode levels as {price: quantity}.
            raw = list(raw.items())
    if not isinstance(raw, (list, tuple)):
        return []

    result = []
    for item in raw:
        try:
            if isinstance(item, dict):
                price = item.get("price", item.get("p"))
                quantity = item.get(
                    "qty", item.get("quantity", item.get("amount", item.get("q")))
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, quantity = item[0], item[1]
            else:
                continue

            price = _number(price)
            quantity = _number(quantity)
            if price > 0 and quantity > 0:
                result.append((price, quantity))
        except (TypeError, ValueError, OverflowError):
            continue
    return result


def parse_order_book(payload):
    """Return (bids, asks), best price first; accept common API envelopes."""
    current = payload
    for _ in range(6):
        if not isinstance(current, dict):
            break

        has_sides = any(key in current for key in ("bids", "asks", "b", "a"))
        if has_sides:
            bids = _levels(current.get("bids", current.get("b", [])))
            asks = _levels(current.get("asks", current.get("a", [])))
            if bids and asks:
                break

        nested = None
        for key in ("data", "result", "payload"):
            candidate = current.get(key)
            if isinstance(candidate, dict) and candidate is not current:
                nested = candidate
                break
        if nested is None:
            break
        current = nested
    else:
        bids = _levels(current.get("bids", current.get("b", []))) if isinstance(current, dict) else []
        asks = _levels(current.get("asks", current.get("a", []))) if isinstance(current, dict) else []

    if not isinstance(current, dict):
        raise ValueError("depth response is not an order-book object")

    bids = _levels(current.get("bids", current.get("b", [])))
    asks = _levels(current.get("asks", current.get("a", [])))
    bids.sort(key=lambda level: level[0], reverse=True)
    asks.sort(key=lambda level: level[0])

    if not bids or not asks:
        keys = ", ".join(sorted(str(key) for key in current))
        raise ValueError("depth response has no usable bids/asks (keys: {})".format(keys))
    if asks[0][0] < bids[0][0]:
        raise ValueError("crossed order book: best ask is below best bid")
    return bids, asks
