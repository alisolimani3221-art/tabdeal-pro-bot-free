import unittest

from multi_symbol_stream_probe import classify_multi_symbol_observation


class MultiSymbolStreamProbeTests(unittest.TestCase):
    def test_confirms_both_symbols_only_when_both_seen_after_second_subscription(self):
        self.assertEqual(
            classify_multi_symbol_observation(
                ("BTC_USDT", "ETH_USDT"),
                {"BTC_USDT": 12, "ETH_USDT": 9},
            ),
            "multi_symbol_confirmed",
        )

    def test_detects_last_subscription_only_observation(self):
        self.assertEqual(
            classify_multi_symbol_observation(
                ("BTC_USDT", "ETH_USDT"),
                {"ETH_USDT": 8},
            ),
            "single_symbol_or_replacement_only",
        )

    def test_missing_or_unrelated_market_data_is_inconclusive(self):
        for observed in ({}, {"BTC_USDT": 3}, {"SOL_USDT": 4}):
            with self.subTest(observed=observed):
                self.assertEqual(
                    classify_multi_symbol_observation(
                        ("BTC_USDT", "ETH_USDT"), observed
                    ),
                    "inconclusive",
                )

    def test_requires_two_distinct_symbols(self):
        for symbols in (("BTC_USDT",), ("BTC_USDT", "BTC_USDT")):
            with self.subTest(symbols=symbols):
                with self.assertRaises(ValueError):
                    classify_multi_symbol_observation(symbols, {})


if __name__ == "__main__":
    unittest.main()
