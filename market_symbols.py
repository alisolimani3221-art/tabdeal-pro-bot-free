"""Resolve and enumerate symbols using Tabdeal Futures exchangeInfo."""


def _normalized_symbol(value):
    return "".join(char for char in str(value).upper() if char.isalnum())


def list_market_symbols(symbols):
    """Return all active USDT perpetual contracts in exchange-native format.

    Fields absent from an exchange response are tolerated, but when present
    they must identify an active USDT perpetual contract.
    """
    if not isinstance(symbols, list):
        raise ValueError("Futures exchangeInfo did not contain a symbols list")

    found = []
    seen = set()
    for item in symbols:
        if isinstance(item, dict):
            actual = item.get("symbol")
            if actual is None:
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
        else:
            actual = item
        actual = str(actual)
        normalized = _normalized_symbol(actual)
        if not normalized.endswith("USDT") or normalized in seen:
            continue
        seen.add(normalized)
        found.append(actual)

    # Keep BTC as the initial/default choice, then list the remaining
    # exchange-supported contracts alphabetically.
    found.sort(key=lambda value: (0 if _normalized_symbol(value) == "BTCUSDT" else 1, value))
    return found


def select_market_symbol(symbols, requested="BTCUSDT"):
    """Return the exchange's exact symbol matching a normalized market pair."""
    wanted = _normalized_symbol(requested)
    for actual in list_market_symbols(symbols):
        if _normalized_symbol(actual) == wanted:
            return str(actual)

    # Preserve compatibility for symbol records whose metadata is incomplete.
    if isinstance(symbols, list):
        for item in symbols:
            if not isinstance(item, dict):
                continue
            actual = item.get("symbol")
            if actual is not None and _normalized_symbol(actual) == wanted:
                return str(actual)
    raise ValueError("{} was not found in Futures exchangeInfo".format(requested))
