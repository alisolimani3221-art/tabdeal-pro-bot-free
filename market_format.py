"""Format market prices and quantities without hiding small values."""

import math


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
    if exponent < -max_decimals:
        mantissa, power = format(number, ".{}e".format(significant_digits - 1)).split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return "{}e{}".format(mantissa, int(power))

    decimals = max(0, min(max_decimals, significant_digits - 1 - exponent))
    rendered = format(number, ",.{}f".format(decimals))
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in ("-0", ""):
        return "0"
    return rendered
