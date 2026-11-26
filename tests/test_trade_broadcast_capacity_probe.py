import unittest

from trade_broadcast_capacity_probe import validate_capacity_symbols


class BroadcastCapacityValidationTests(unittest.TestCase):
    def test_accepts_unique_exchange_native_symbols(self):
        self.assertEqual(
            validate_capacity_symbols(["btc_usdt", "ETH_USDT"]),
            ("BTC_USDT", "ETH_USDT"),
        )

    def test_rejects_too_few_or_too_many_symbols(self):
        for symbols in ([], ["BTC_USDT"], ["S{}_USDT".format(i) for i in range(21)]):
            with self.subTest(count=len(symbols)), self.assertRaises(ValueError):
                validate_capacity_symbols(symbols)

    def test_rejects_invalid_or_duplicate_symbols(self):
        for symbols in (
            ["BTC_USDT", "BTC_USDT"],
            ["BTC/USDT", "ETH_USDT"],
            ["BTCUSDT", "ETH_USDT"],
            ["BTC_USDT", None],
        ):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                validate_capacity_symbols(symbols)


if __name__ == "__main__":
    unittest.main()
