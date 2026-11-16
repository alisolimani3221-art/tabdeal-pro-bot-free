import json
import unittest

from trade_stream_probe import parse_trade_message


class TradeStreamProbeTests(unittest.TestCase):
    def test_documented_trade_envelope_with_common_fields(self):
        payload = {"trade": {"symbol": "AEVO_USDT", "price": "1.25",
                             "quantity": "4", "timestamp": 1700000000}}
        result = parse_trade_message(json.dumps(payload), "AEVO_USDT")
        self.assertTrue(result["ok"])
        self.assertEqual(result["price"], 1.25)
        self.assertEqual(result["quantity"], 4.0)
        self.assertEqual(result["timestamp_ms"], 1700000000000)

    def test_millisecond_timestamp_is_preserved(self):
        payload = {"trade": {"s": "AEVO_USDT", "p": "2",
                             "q": "3", "T": 1700000000123}}
        result = parse_trade_message(json.dumps(payload), "AEVO_USDT")
        self.assertTrue(result["ok"])
        self.assertEqual(result["timestamp_ms"], 1700000000123)

    def test_unknown_schema_is_not_guessed(self):
        result = parse_trade_message('{"trade":{"foo":"bar"}}', "AEVO_USDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "trade_schema_unrecognized")

    def test_rejects_bad_values_and_symbol_mismatch(self):
        bad = {"trade": {"symbol": "AEVO_USDT", "price": "NaN",
                         "quantity": "1", "timestamp": "1700000000"}}
        self.assertFalse(parse_trade_message(json.dumps(bad), "AEVO_USDT")["ok"])
        other = {"trade": {"symbol": "BTC_USDT", "price": "1",
                           "quantity": "1", "timestamp": "1700000000"}}
        self.assertEqual(parse_trade_message(json.dumps(other), "AEVO_USDT")["reason"],
                         "symbol_mismatch")

    def test_rejects_invalid_envelopes(self):
        self.assertEqual(parse_trade_message('{"error":"Invalid params"}')["reason"],
                         "exchange_error")
        self.assertEqual(parse_trade_message("not json")["reason"], "invalid_json")


if __name__ == "__main__":
    unittest.main()
