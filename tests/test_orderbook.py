import json
import unittest

from orderbook import parse_order_book
from market_symbols import select_market_symbol


class OrderBookTests(unittest.TestCase):
    def test_tabdeal_documented_shape_and_sorting(self):
        bids, asks = parse_order_book({
            "lastUpdateId": 123,
            "bids": [["100.25", "2.5"], ["102", "1"]],
            "asks": [["105", "3"], ["103", "4"]],
        })
        self.assertEqual(bids[0], (102.0, 1.0))
        self.assertEqual(asks[0], (103.0, 4.0))

    def test_nested_short_keys(self):
        bids, asks = parse_order_book({"data": {"b": [["10", "2"]], "a": [["11", "3"]]}})
        self.assertEqual(bids, [(10.0, 2.0)])
        self.assertEqual(asks, [(11.0, 3.0)])

    def test_nested_json_string(self):
        data = {"bids": [["10", "2"]], "asks": [["11", "3"]]}
        bids, asks = parse_order_book({"code": 0, "data": json.dumps(data)})
        self.assertEqual(bids, [(10.0, 2.0)])
        self.assertEqual(asks, [(11.0, 3.0)])

    def test_case_insensitive_object_levels(self):
        bids, asks = parse_order_book({
            "result": {"BIDS": [{"Price": "20", "Size": "1.5"}],
                       "ASKS": [{"p": "21", "volume": "2"}]}
        })
        self.assertEqual(bids, [(20.0, 1.5)])
        self.assertEqual(asks, [(21.0, 2.0)])

    def test_mapping_levels_and_duplicate_aggregation(self):
        bids, asks = parse_order_book({
            "bids": {"10": "1", "11": "2", "11.0": "3"},
            "asks": [["12", "2"], ["12", "3"]],
        })
        self.assertEqual(bids, [(11.0, 5.0), (10.0, 1.0)])
        self.assertEqual(asks, [(12.0, 5.0)])

    def test_empty_outer_side_falls_back_to_nested(self):
        bids, asks = parse_order_book({
            "bids": None, "asks": None,
            "data": {"bids": [["10", "2"]], "asks": [["11", "3"]]},
        })
        self.assertEqual(bids, [(10.0, 2.0)])
        self.assertEqual(asks, [(11.0, 3.0)])

    def test_ignores_zero_and_invalid_levels(self):
        bids, asks = parse_order_book({
            "bids": [["101", "0"], ["100", "2"], ["nan", "1"]],
            "asks": [["102", "3"], ["103", "0"]],
        })
        self.assertEqual(bids, [(100.0, 2.0)])
        self.assertEqual(asks, [(102.0, 3.0)])

    def test_api_error_is_reported(self):
        with self.assertRaisesRegex(ValueError, "depth API error 1001"):
            parse_order_book({"code": 1001, "msg": "invalid symbol", "data": {}})

    def test_rejects_empty_and_crossed_books(self):
        with self.assertRaises(ValueError):
            parse_order_book({"bids": [], "asks": []})
        with self.assertRaises(ValueError):
            parse_order_book({"bids": [["12", "1"]], "asks": [["11", "1"]]})
        with self.assertRaises(ValueError):
            parse_order_book({"bids": [["nan", "1"]], "asks": [["11", "1"]]})


class MarketSymbolTests(unittest.TestCase):
    def test_resolves_tabdeal_underscore_symbol(self):
        self.assertEqual(select_market_symbol(
            [{"symbol": "ETH_USDT"}, {"symbol": "BTC_USDT"}]), "BTC_USDT")

    def test_resolves_compact_symbol(self):
        self.assertEqual(select_market_symbol([{"symbol": "BTCUSDT"}]), "BTCUSDT")

    def test_rejects_missing_symbol(self):
        with self.assertRaises(ValueError):
            select_market_symbol([{"symbol": "ETH_USDT"}])


if __name__ == "__main__":
    unittest.main()
