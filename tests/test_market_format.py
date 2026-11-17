import math
import unittest

from market_format import format_market_value


class MarketFormatTests(unittest.TestCase):
    def test_small_coin_price_is_not_rounded_to_zero(self):
        self.assertEqual(format_market_value(0.0000123456789), "0.000012345679")

    def test_small_order_quantity_keeps_precision(self):
        self.assertEqual(format_market_value(0.00000012), "0.00000012")

    def test_normal_price_uses_eight_significant_digits(self):
        self.assertEqual(format_market_value(0.123456789), "0.12345679")

    def test_large_values_keep_grouping(self):
        self.assertEqual(format_market_value(123456.789), "123,456.79")

    def test_trailing_zeroes_are_removed(self):
        self.assertEqual(format_market_value(1.25), "1.25")
        self.assertEqual(format_market_value(1000), "1,000")

    def test_scientific_notation_preserves_significant_digits_at_decimal_limit(self):
        self.assertEqual(format_market_value(0.000000123456789), "1.2345679e-7")
        self.assertEqual(format_market_value(0.00000000000123456789), "1.2345679e-12")
        self.assertEqual(format_market_value(0.00123456789, max_decimals=5), "1.2345679e-3")

    def test_extremely_small_values_use_compact_scientific_notation(self):
        self.assertEqual(format_market_value(0.0000000000001), "1e-13")

    def test_negative_values_are_formatted_without_losing_sign(self):
        self.assertEqual(format_market_value(-0.0000123456789), "-0.000012345679")

    def test_zero_is_stable(self):
        self.assertEqual(format_market_value(0), "0")
        self.assertEqual(format_market_value(-0.0), "0")

    def test_non_numeric_and_non_finite_values_are_rejected(self):
        for value in ("not-a-number", None, math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    format_market_value(value)

    def test_precision_arguments_are_validated(self):
        with self.assertRaises(ValueError):
            format_market_value(1, significant_digits=0)
        with self.assertRaises(ValueError):
            format_market_value(1, max_decimals=-1)


if __name__ == "__main__":
    unittest.main()
