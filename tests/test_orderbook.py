import unittest

from orderbook import parse_order_book
from market_symbols import select_market_symbol


class OrderBookTests(unittest.TestCase):
    def test_binance_style_arrays_are_sorted(self):
        bids, asks = parse_order_book({
            "bids": [["100", "2"], ["102", "1"]],
            "asks": [["105", "3"], ["103", "4"]],
        })
        self.assertEqual(bids[0], (102.0, 1.0))
        self.assertEqual(asks[0], (103.0, 4.0))

    def test_nested_and_short_keys(self):
        bids, asks = parse_order_book({
            "data": {"b": [["10", "2"]], "a": [["11", "3"]]}
        })
        self.assertEqual(bids, [(10.0, 2.0)])
        self.assertEqual(asks, [(11.0, 3.0)])

    def test_object_levels(self):
        bids, asks = parse_order_book({
            "result": {
                "bids": [{"price": "20", "qty": "1.5"}],
                "asks": [{"p": "21", "q": "2"}],
            }
        })
        self.assertEqual(bids, [(20.0, 1.5)])
        self.assertEqual(asks, [(21.0, 2.0)])

    def test_rejects_empty_or_invalid_book(self):
        with self.assertRaises(ValueError):
            parse_order_book({"bids": [], "asks": []})
        with self.assertRaises(ValueError):
            parse_order_book({"bids": [["nan", "1"]], "asks": [["11", "1"]]})

    def test_ignores_zero_quantity_levels(self):
        bids, asks = parse_order_book({
            "bids": [["101", "0"], ["100", "2"]],
            "asks": [["102", "3"], ["103", "0"]],
        })
        self.assertEqual(bids, [(100.0, 2.0)])
        self.assertEqual(asks, [(102.0, 3.0)])

    def test_rejects_book_with_only_zero_quantity(self):
        with self.assertRaises(ValueError):
            parse_order_book({
                "bids": [["100", "0"]],
                "asks": [["101", "0"]],
            })

    def test_rejects_crossed_book(self):
        with self.assertRaises(ValueError):
            parse_order_book({"bids": [["12", "1"]], "asks": [["11", "1"]]})



    def test_empty_outer_sides_fall_back_to_nested_book(self):
        bids, asks = parse_order_book({
            "bids": None,
            "asks": None,
            "data": {"bids": [["100", "2"]], "asks": [["101", "3"]]},
        })
        self.assertEqual(bids, [(100.0, 2.0)])
        self.assertEqual(asks, [(101.0, 3.0)])

    def test_single_object_level_is_supported(self):
        bids, asks = parse_order_book({
            "bids": {"price": "100", "qty": "2"},
            "asks": {"price": "101", "qty": "3"},
        })
        self.assertEqual(bids, [(100.0, 2.0)])
        self.assertEqual(asks, [(101.0, 3.0)])


class MarketSymbolTests(unittest.TestCase):
    def test_resolves_tabdeal_underscore_symbol(self):
        symbols = [{"symbol": "ETH_USDT"}, {"symbol": "BTC_USDT"}]
        self.assertEqual(select_market_symbol(symbols), "BTC_USDT")

    def test_does_not_select_inactive_bitcoin_contract(self):
        symbols = [
            {"symbol": "BTC_USDT", "quoteAsset": "USDT", "status": "BREAK",
             "contractType": "PERPETUAL"},
            {"symbol": "ETH_USDT", "quoteAsset": "USDT", "status": "TRADING",
             "contractType": "PERPETUAL"},
        ]
        with self.assertRaises(ValueError):
            select_market_symbol(symbols, "BTCUSDT")

    def test_resolves_compact_symbol(self):
        self.assertEqual(select_market_symbol([{"symbol": "BTCUSDT"}]), "BTCUSDT")

    def test_rejects_missing_symbol(self):
        with self.assertRaises(ValueError):
            select_market_symbol([{"symbol": "ETH_USDT"}])


if __name__ == "__main__":
    unittest.main()
