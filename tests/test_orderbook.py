import json
import unittest

from orderbook import parse_order_book
from market_symbols import list_market_symbols, select_market_symbol


class OrderBookTests(unittest.TestCase):
    def valid_book(self):
        return {"bids": [["100", "2"]], "asks": [["101", "3"]]}

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
        self.assertEqual(parse_order_book({"code": 0, "data": json.dumps(data)}),
                         ([(10.0, 2.0)], [(11.0, 3.0)]))

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

    def test_empty_outer_arrays_fall_back_to_nested(self):
        bids, asks = parse_order_book({
            "bids": [], "asks": [],
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

    def test_rejects_boolean_and_container_numbers(self):
        payload = {"bids": [[True, "1"], ["100", False], [[], "2"]],
                   "asks": [["101", "1"]]}
        with self.assertRaisesRegex(ValueError, "no usable bids/asks"):
            parse_order_book(payload)

    def test_rejects_non_finite_and_negative_levels(self):
        payload = {"bids": [["NaN", "1"], ["99", "Infinity"], ["-1", "1"]],
                   "asks": [["101", "1"]]}
        with self.assertRaisesRegex(ValueError, "no usable bids/asks"):
            parse_order_book(payload)

    def test_duplicate_aggregation_overflow_drops_entire_price_level(self):
        payload = {"bids": [["100", "1e308"], ["100", "1e308"]],
                   "asks": [["101", "1"]]}
        with self.assertRaisesRegex(ValueError, "no usable bids/asks"):
            parse_order_book(payload)

    def test_api_error_is_reported(self):
        with self.assertRaisesRegex(ValueError, "depth API error 1001"):
            parse_order_book({"code": 1001, "msg": "invalid symbol", "data": {}})

    def test_rejects_empty_and_crossed_books(self):
        for payload in (
            {"bids": [], "asks": []},
            {"bids": [["12", "1"]], "asks": [["11", "1"]]},
            {"bids": [["nan", "1"]], "asks": [["11", "1"]]},
            {"bids": [["10", "1"]], "asks": []},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_order_book(payload)

    def test_rejects_best_prices_that_overflow_midpoint_arithmetic(self):
        payload = {"bids": [["1e308", "1"]], "asks": [["1.7e308", "1"]]}
        with self.assertRaisesRegex(ValueError, "arithmetic"):
            parse_order_book(payload)

    def test_accepts_locked_book_at_equal_best_prices(self):
        bids, asks = parse_order_book({"bids": [["10", "1"]], "asks": [["10", "2"]]})
        self.assertEqual(bids[0][0], asks[0][0])

    def test_rejects_non_object_payload(self):
        for payload in (None, [], "bad", 1):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_order_book(payload)


class MarketSymbolTests(unittest.TestCase):
    def test_resolves_tabdeal_underscore_symbol(self):
        self.assertEqual(select_market_symbol(
            [{"symbol": "ETH_USDT"}, {"symbol": "BTC_USDT"}]), "BTC_USDT")

    def test_resolves_compact_symbol(self):
        self.assertEqual(select_market_symbol([{"symbol": "BTCUSDT"}]), "BTCUSDT")

    def test_rejects_missing_symbol(self):
        with self.assertRaises(ValueError):
            select_market_symbol([{"symbol": "ETH_USDT"}])

    def test_filters_inactive_non_usdt_and_non_perpetual(self):
        symbols = [
            {"symbol": "AAA_USDT", "quoteAsset": "USDT", "status": "TRADING",
             "contractType": "PERPETUAL"},
            {"symbol": "BBB_USDT", "quoteAsset": "USDT", "status": "BREAK"},
            {"symbol": "CCC_BTC", "quoteAsset": "BTC", "status": "TRADING"},
            {"symbol": "DDD_USDT", "quoteAsset": "USDT", "status": "TRADING",
             "contractType": "CURRENT_QUARTER"},
        ]
        self.assertEqual(list_market_symbols(symbols), ["AAA_USDT"])

    def test_deduplicates_compact_and_underscored_symbols(self):
        self.assertEqual(list_market_symbols(["BTCUSDT", "BTC_USDT"]), ["BTCUSDT"])

    def test_rejects_malformed_market_symbols_instead_of_sanitizing_them(self):
        malformed = ["BTC/USDT", "BTC-USDT", "BTC__USDT", "BTC USDT", "_USDT", "BTC_USDT!"]
        self.assertEqual(list_market_symbols(malformed), [])
        self.assertEqual(list_market_symbols([{"symbol": "BTC/USDT"}]), [])

    def test_rejects_invalid_requested_symbol(self):
        for requested in ("BTC/USDT", "BTC__USDT", "BTC_USDT!"):
            with self.subTest(requested=requested):
                with self.assertRaises(ValueError):
                    select_market_symbol(["BTC_USDT"], requested)

    def test_rejects_non_string_symbol_values(self):
        self.assertEqual(list_market_symbols([None, 123, True, {"symbol": 123}]), [])

    def test_rejects_non_list_exchange_info(self):
        for value in (None, {}, "BTC_USDT"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    list_market_symbols(value)


if __name__ == "__main__":
    unittest.main()
