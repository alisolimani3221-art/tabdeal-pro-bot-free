"""Safe selection of verified futures symbols for one broadcast socket."""

import re

_SYMBOL = re.compile(r"^[A-Z0-9]+_USDT$")
MAX_VERIFIED_BROADCAST_SYMBOLS = 2


def select_broadcast_symbols(selected_symbol, verified_symbols,
                             max_symbols=MAX_VERIFIED_BROADCAST_SYMBOLS):
    """Return selected symbol first, plus at most one verified secondary market."""
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
