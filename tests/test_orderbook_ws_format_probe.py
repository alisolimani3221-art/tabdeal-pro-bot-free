import unittest

from orderbook_ws_format_probe import (
    _ack_error, _depth_data, _format_for_symbol, _normalize_symbol,
    build_candidates,
)


class OrderBookWsFormatProbeTests(unittest.TestCase):
    def test_candidate_matrix_contains_symbol_and_channel_variants(self):
        values = build_candidates("BTC_USDT")
        self.assertEqual(len(values), 16)
        for value in ("btcusdt@depth@2000ms", "BTCUSDT@depth@2000ms",
                      "BTC_USDT@depth@2000ms", "btc_usdt@depth@2000ms",
                      "btcusdt@depth10@100ms"):
            self.assertIn(value, values)

    def test_eth_candidate_matrix(self):
        values = build_candidates("ETH_USDT")
        self.assertIn("ethusdt@depth@2000ms", values)
        self.assertIn("ETH_USDT@depth@2000ms", values)
        self.assertIn("eth_usdt@depth@2000ms", values)

    def test_invalid_symbol_rejected(self):
        with self.assertRaises(ValueError):
            build_candidates("BTC/USDT")

    def test_ack_error_detection(self):
        self.assertEqual(_ack_error({"id": 7, "result": "[{'error': 'INVALID_FORMAT'}]"}),
                         "INVALID_FORMAT")
        self.assertIsNone(_ack_error({"id": 7, "result": None}))

    def test_depth_data_supports_full_and_diff_shapes(self):
        self.assertEqual(_depth_data({"s": "BTCUSDT", "b": [], "a": []})["s"], "BTCUSDT")
        self.assertEqual(_depth_data({"data": {"symbol": "BTC_USDT",
                                               "bids": [], "asks": []}})["symbol"],
                         "BTC_USDT")
        self.assertIsNone(_depth_data({"s": "BTCUSDT", "b": []}))

    def test_symbol_normalization(self):
        self.assertEqual(_normalize_symbol("btcusdt"), "BTC_USDT")
        self.assertEqual(_normalize_symbol("BTC_USDT"), "BTC_USDT")
        self.assertIsNone(_normalize_symbol("BTCUSD"))

    def test_second_symbol_preserves_stream_format(self):
        self.assertEqual(_format_for_symbol("btcusdt@depth@2000ms", "ETH_USDT"),
                         "ethusdt@depth@2000ms")
        self.assertEqual(_format_for_symbol("BTC_USDT@depth@2000ms", "ETH_USDT"),
                         "ETH_USDT@depth@2000ms")
        self.assertEqual(_format_for_symbol("btc_usdt@depth@2000ms", "ETH_USDT"),
                         "eth_usdt@depth@2000ms")


if __name__ == "__main__":
    unittest.main()
