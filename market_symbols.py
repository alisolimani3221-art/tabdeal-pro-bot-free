"""Resolve and enumerate symbols using Tabdeal Futures exchangeInfo."""
import re

# Tabdeal's USDT-margined futures symbols use either BASE_USDT or BASEUSDT.
# Do not silently erase arbitrary punctuation: that can turn malformed input
# into a different, apparently valid market identifier.
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+_?USDT$")


def _normalized_symbol(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if not _SYMBOL_RE.fullmatch(candidate):
        return None
    return candidate.replace("_", "")


def list_market_symbols(symbols):
    """Return unique active USDT perpetual contracts in exchange-native form.

    String entries are supported for simplified/test payloads. For object
    entries, any supplied quote/status/contract metadata must agree with an
    active USDT perpetual contract; malformed symbols are always discarded.
    """
    if not isinstance(symbols, list):
        raise ValueError("Futures exchangeInfo did not contain a symbols list")

    found = []
    seen = set()
    for item in symbols:
        if isinstance(item, dict):
            actual = item.get("symbol")
            if not isinstance(actual, str):
                continue
            quote = item.get("quoteAsset")
            status = item.get("status")
            contract = item.get("contractType")
            if quote is not None and str(quote).upper() != "USDT":
                continue
            if status is not None and str(status).upper() not in ("TRADING", "ACTIVE"):
                continue
            if contract is not None and str(contract).upper() not in ("PERPETUAL", "PERP"):
                continue
        elif isinstance(item, str):
            actual = item
        else:
            continue

        normalized = _normalized_symbol(actual)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        found.append(actual.strip())

    # Keep BTC as the initial/default choice, then list the remaining
    # exchange-supported contracts alphabetically.
    found.sort(key=lambda value: (0 if _normalized_symbol(value) == "BTCUSDT" else 1, value))
    return found


def select_market_symbol(symbols, requested="BTCUSDT"):
    """Return the exchange's exact symbol matching a valid normalized pair."""
    wanted = _normalized_symbol(requested)
    if wanted is None:
        raise ValueError("requested symbol must be a valid USDT futures symbol")
    for actual in list_market_symbols(symbols):
        if _normalized_symbol(actual) == wanted:
            return str(actual)

    raise ValueError("{} was not found in Futures exchangeInfo".format(requested))
