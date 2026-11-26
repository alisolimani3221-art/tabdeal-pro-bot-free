"""Safe selection of verified futures symbols for one broadcast socket."""

import re

_SYMBOL = re.compile(r"^[A-Z0-9]+_USDT$")
MAX_VERIFIED_BROADCAST_SYMBOLS = 5


def select_broadcast_symbols(selected_symbol, verified_symbols,
                             max_symbols=MAX_VERIFIED_BROADCAST_SYMBOLS):
    """Return selected symbol first, plus at most four verified secondary markets."""
    if not isinstance(selected_symbol, str):
        raise ValueError("selected symbol must be a string")
    selected = selected_symbol.strip().upper()
    if not _SYMBOL.fullmatch(selected):
        raise ValueError("selected symbol is not exchange-native")
    if isinstance(max_symbols, bool) or not isinstance(max_symbols, int):
        raise ValueError("max_symbols must be an integer")
    if not 1 <= max_symbols <= MAX_VERIFIED_BROADCAST_SYMBOLS:
        raise ValueError("max_symbols exceeds the verified broadcast limit")
    if not isinstance(verified_symbols, (set, list, tuple)):
        verified_symbols = ()
    verified = set()
    for value in verified_symbols:
        if isinstance(value, str):
            candidate = value.strip().upper()
            if _SYMBOL.fullmatch(candidate):
                verified.add(candidate)
    # Never drop the operator-selected contract, even if discovery failed.
    verified.discard(selected)
    secondary = sorted(verified)[:max_symbols - 1]
    return tuple([selected] + secondary)



def include_required_symbol(symbols, required_symbol,
                           max_symbols=MAX_VERIFIED_BROADCAST_SYMBOLS):
    """Keep the selected market first and protect a required open-position market."""
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise ValueError("at least one selected market is required")
    if isinstance(max_symbols, bool) or not isinstance(max_symbols, int):
        raise ValueError("max_symbols must be an integer")
    if not 1 <= max_symbols <= MAX_VERIFIED_BROADCAST_SYMBOLS:
        raise ValueError("max_symbols exceeds the verified broadcast limit")
    normalized = []
    for value in symbols:
        if not isinstance(value, str):
            raise ValueError("subscription symbol must be a string")
        candidate = value.strip().upper()
        if not _SYMBOL.fullmatch(candidate) or candidate in normalized:
            raise ValueError("subscription symbol is invalid or duplicated")
        normalized.append(candidate)
    if not isinstance(required_symbol, str):
        raise ValueError("required symbol must be a string")
    required = required_symbol.strip().upper()
    if not _SYMBOL.fullmatch(required):
        raise ValueError("required symbol is not exchange-native")
    if required in normalized:
        return tuple(normalized[:max_symbols])
    # Preserve the UI-selected first symbol, then reserve a slot for the
    # recovered open position before filling remaining slots from discovery.
    return tuple(([normalized[0], required] + normalized[1:max_symbols - 1])
                 [:max_symbols])


def subscribe_broadcast_symbols(websocket, symbols):
    """Send each validated market as its own plain-text subscription frame."""
    if not callable(getattr(websocket, "send", None)):
        raise ValueError("websocket must provide send()")
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise ValueError("at least one subscription symbol is required")
    if len(symbols) > MAX_VERIFIED_BROADCAST_SYMBOLS:
        raise ValueError("subscription count exceeds the verified broadcast limit")
    normalized = []
    for value in symbols:
        if not isinstance(value, str):
            raise ValueError("subscription symbol must be a string")
        candidate = value.strip().upper()
        if not _SYMBOL.fullmatch(candidate) or candidate in normalized:
            raise ValueError("subscription symbol is invalid or duplicated")
        normalized.append(candidate)
    for candidate in normalized:
        websocket.send(candidate)
    return tuple(normalized)
