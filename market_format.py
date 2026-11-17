"""Format market prices and quantities without hiding small values."""

import math
from decimal import Decimal


def format_market_value(value, significant_digits=8, max_decimals=12):
    """Format a finite market value with useful significant-digit precision.

    Fixed two-decimal formatting turns many low-priced futures contracts into
    0.00. This formatter keeps up to eight significant digits, trims trailing
    zeroes, and switches to scientific notation only when decimal notation
    would exceed the configured precision limit.
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("market value must be numeric") from exc

    if not math.isfinite(number):
        raise ValueError("market value must be finite")
    if not isinstance(significant_digits, int) or significant_digits < 1:
        raise ValueError("significant_digits must be a positive integer")
    if not isinstance(max_decimals, int) or max_decimals < 0:
        raise ValueError("max_decimals must be a non-negative integer")
    if number == 0:
        return "0"

    exponent = math.floor(math.log10(abs(number)))
    required_decimals = significant_digits - 1 - exponent
    # A small value may have only a few significant digits (for example,
    # 0.00000012). Keep that exact decimal form when it fits the limit instead
    # of needlessly converting it to scientific notation.
    actual_decimal_places = max(0, -Decimal(str(number)).as_tuple().exponent)
    if required_decimals > max_decimals and actual_decimal_places > max_decimals:
        # Decimal notation would discard significant digits beyond the
        # configured limit, so preserve the value in compact scientific form.
        mantissa, power = format(number, ".{}e".format(significant_digits - 1)).split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return "{}e{}".format(mantissa, int(power))

    decimals = actual_decimal_places if required_decimals > max_decimals else max(0, required_decimals)
    rendered = format(number, ",.{}f".format(decimals))
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in ("-0", ""):
        return "0"
    return rendered
