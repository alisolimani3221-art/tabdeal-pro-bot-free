import json
import unittest

from trade_stream_probe import MAX_MESSAGE_BYTES, parse_trade_message


class TradeStreamProbeTests(unittest.TestCase):
    def valid_trade(self):
        return {"trade": {"symbol": "AEVO_USDT", "price": "1.25",
                          "quantity": "4", "timestamp": 1700000000}}

    def test_documented_trade_envelope_with_common_fields(self):
        result = parse_trade_message(json.dumps(self.valid_trade()), "AEVO_USDT")
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

    def test_rejects_boolean_in_each_numeric_trade_field(self):
        for field in ("price", "quantity", "timestamp"):
            with self.subTest(field=field):
                payload = self.valid_trade()
                payload["trade"][field] = True
                result = parse_trade_message(json.dumps(payload), "AEVO_USDT")
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "trade_schema_unrecognized")

    def test_rejects_false_boolean_in_each_numeric_trade_field(self):
        for field in ("price", "quantity", "timestamp"):
            with self.subTest(field=field):
                payload = self.valid_trade()
                payload["trade"][field] = False
                self.assertFalse(parse_trade_message(json.dumps(payload), "AEVO_USDT")["ok"])

    def test_invalid_utf8_bytes_are_rejected_without_raising(self):
        result = parse_trade_message(b'{"trade":\xff}')
        self.assertEqual(result, {"ok": False, "reason": "invalid_utf8"})

    def test_oversized_bytes_are_rejected_before_decode(self):
        result = parse_trade_message(b"x" * (MAX_MESSAGE_BYTES + 1))
        self.assertEqual(result, {"ok": False, "reason": "message_too_large"})

    def test_oversized_text_is_rejected(self):
        result = parse_trade_message(" " * (MAX_MESSAGE_BYTES + 1))
        self.assertEqual(result, {"ok": False, "reason": "message_too_large"})

    def test_lone_surrogate_text_is_rejected_without_raising(self):
        result = parse_trade_message('{"trade":"\ud800"}')
        self.assertEqual(result, {"ok": False, "reason": "invalid_utf8"})

    def test_rejects_invalid_envelopes(self):
        self.assertEqual(parse_trade_message('{"error":"Invalid params"}')["reason"],
                         "exchange_error")
        self.assertEqual(parse_trade_message("not json")["reason"], "invalid_json")
        self.assertEqual(parse_trade_message(None)["reason"],
                         "invalid_or_oversized_message")


if __name__ == "__main__":
    unittest.main()
