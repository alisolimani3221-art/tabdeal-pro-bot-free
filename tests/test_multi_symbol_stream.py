import unittest

from multi_symbol_stream import (
    MAX_VERIFIED_BROADCAST_SYMBOLS,
    select_broadcast_symbols,
    include_required_symbol,
    subscribe_broadcast_symbols,
)


class MultiSymbolBroadcastSelectionTests(unittest.TestCase):
    def test_selected_symbol_is_always_first(self):
        self.assertEqual(
            select_broadcast_symbols("ETH_USDT", {"BTC_USDT", "SOL_USDT"}),
            ("ETH_USDT", "BTC_USDT"),
        )

    def test_limits_to_five_verified_markets(self):
        result = select_broadcast_symbols(
            "BTC_USDT", {"ETH_USDT", "SOL_USDT", "XRP_USDT", "BNB_USDT", "ADA_USDT", "DOGE_USDT"}
        )
        self.assertEqual(result, ("BTC_USDT", "ADA_USDT", "BNB_USDT", "ETH_USDT", "SOL_USDT"))
        self.assertEqual(len(result), MAX_VERIFIED_BROADCAST_SYMBOLS)

    def test_recovered_open_position_gets_reserved_slot_without_exceeding_five(self):
        selected = ("BTC_USDT", "ADA_USDT", "BNB_USDT", "ETH_USDT", "SOL_USDT")
        result = include_required_symbol(selected, "XRP_USDT")
        self.assertEqual(
            result, ("BTC_USDT", "XRP_USDT", "ADA_USDT", "BNB_USDT", "ETH_USDT")
        )
        self.assertEqual(len(result), 5)
        self.assertEqual(include_required_symbol(result, "XRP_USDT"), result)

    def test_invalid_required_market_is_rejected(self):
        with self.assertRaises(ValueError):
            include_required_symbol(("BTC_USDT",), "BTC/USDT")

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
                "BTC_USDT", {"ETH_USDT", "SOL_USDT"}, max_symbols=6
            )


    def test_sends_each_symbol_as_separate_plain_text_frame(self):
        class FakeSocket:
            def __init__(self):
                self.sent = []
            def send(self, value):
                self.sent.append(value)

        socket = FakeSocket()
        result = subscribe_broadcast_symbols(
            socket, ("BTC_USDT", "ETH_USDT", "XRP_USDT", "BNB_USDT", "SOL_USDT")
        )
        self.assertEqual(result, ("BTC_USDT", "ETH_USDT", "XRP_USDT", "BNB_USDT", "SOL_USDT"))
        self.assertEqual(socket.sent, ["BTC_USDT", "ETH_USDT", "XRP_USDT", "BNB_USDT", "SOL_USDT"])

    def test_rejects_unsupported_or_duplicate_subscriptions(self):
        class FakeSocket:
            def send(self, value):
                raise AssertionError("must not send invalid subscription")

        for values in ((), ("BTC_USDT", "BTC_USDT"),
                       ("BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "BNB_USDT", "ADA_USDT")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                subscribe_broadcast_symbols(FakeSocket(), values)


if __name__ == "__main__":
    unittest.main()
