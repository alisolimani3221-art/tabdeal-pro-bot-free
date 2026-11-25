import unittest

from multi_symbol_stream import (
    MAX_VERIFIED_BROADCAST_SYMBOLS,
    select_broadcast_symbols,
)


class MultiSymbolBroadcastSelectionTests(unittest.TestCase):
    def test_selected_symbol_is_always_first(self):
        self.assertEqual(
            select_broadcast_symbols("ETH_USDT", {"BTC_USDT", "SOL_USDT"}),
            ("ETH_USDT", "BTC_USDT"),
        )

    def test_limits_to_two_verified_markets(self):
        result = select_broadcast_symbols(
            "BTC_USDT", {"ETH_USDT", "SOL_USDT", "XRP_USDT"}
        )
        self.assertEqual(result, ("BTC_USDT", "ETH_USDT"))
        self.assertEqual(len(result), MAX_VERIFIED_BROADCAST_SYMBOLS)

    def test_unverified_or_malformed_symbols_are_ignored(self):
        self.assertEqual(
            select_broadcast_symbols(
                "BTC_USDT", {"ETH_USDT", "BTCUSDT", "BAD/USDT", None}
            ),
            ("BTC_USDT", "ETH_USDT"),
        )

    def test_discovery_failure_keeps_selected_market(self):
        self.assertEqual(select_broadcast_symbols("BTC_USDT", set()), ("BTC_USDT",))

    def test_invalid_selected_symbol_rejected(self):
        for value in ("BTCUSDT", "BTC/USDT", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                select_broadcast_symbols(value, {"ETH_USDT"})

    def test_limit_cannot_exceed_verified_probe(self):
        with self.assertRaises(ValueError):
            select_broadcast_symbols(
                "BTC_USDT", {"ETH_USDT", "SOL_USDT"}, max_symbols=3
            )


if __name__ == "__main__":
    unittest.main()
