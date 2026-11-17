import json
import math
import unittest

from trade_stream_probe import MAX_MESSAGE_BYTES, parse_trade_message


class TradeStreamProbeTests(unittest.TestCase):
    def valid_trade(self):
        return {"trade": {"symbol": "AEVO_USDT", "price": "1.25",
                          "quantity": "4", "timestamp": 1700000000}}

    def parse_trade_with(self, field, value):
        payload = self.valid_trade()
        payload["trade"][field] = value
        return parse_trade_message(json.dumps(payload), "AEVO_USDT")

    def test_documented_trade_envelope_with_common_fields(self):
        result = parse_trade_message(json.dumps(self.valid_trade()), "AEVO_USDT")
        self.assertTrue(result["ok"])
        self.assertEqual(result["price"], 1.25)
        self.assertEqual(result["quantity"], 4.0)
        self.assertEqual(result["timestamp_ms"], 1700000000000)

    def test_valid_utf8_bytes_are_supported(self):
        raw = json.dumps(self.valid_trade()).encode("utf-8")
        self.assertTrue(parse_trade_message(raw, "AEVO_USDT")["ok"])

    def test_millisecond_timestamp_is_preserved(self):
        payload = {"trade": {"s": "AEVO_USDT", "p": "2",
                             "q": "3", "T": 1700000000123}}
        result = parse_trade_message(json.dumps(payload), "AEVO_USDT")
        self.assertTrue(result["ok"])
        self.assertEqual(result["timestamp_ms"], 1700000000123)

    def test_all_supported_field_aliases_are_accepted(self):
        payload = {"trade": {"symbol": "AEVO_USDT", "tradePrice": "1.5",
                             "amount": "2.5", "tradeTime": 1700000000}}
        self.assertTrue(parse_trade_message(json.dumps(payload), "AEVO_USDT")["ok"])

    def test_unknown_schema_is_not_guessed(self):
        result = parse_trade_message('{"trade":{"foo":"bar"}}', "AEVO_USDT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "trade_schema_unrecognized")

    def test_rejects_boolean_in_each_numeric_trade_field(self):
        for field in ("price", "quantity", "timestamp"):
            with self.subTest(field=field):
                self.assertEqual(self.parse_trade_with(field, True)["reason"],
                                 "trade_schema_unrecognized")

    def test_rejects_false_boolean_in_each_numeric_trade_field(self):
        for field in ("price", "quantity", "timestamp"):
            with self.subTest(field=field):
                self.assertFalse(self.parse_trade_with(field, False)["ok"])

    def test_rejects_null_and_container_values_in_numeric_fields(self):
        invalid_values = (None, [], {}, [1], {"value": 1})
        for field in ("price", "quantity", "timestamp"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    self.assertFalse(self.parse_trade_with(field, value)["ok"])

    def test_rejects_zero_negative_nan_and_infinite_numeric_fields(self):
        invalid_values = (0, -1, "0", "-2.5", "NaN", "Infinity", "-Infinity")
        for field in ("price", "quantity", "timestamp"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    self.assertFalse(self.parse_trade_with(field, value)["ok"])

    def test_rejects_non_numeric_strings(self):
        for value in ("", " ", "abc", "1,25", "0x10"):
            with self.subTest(value=value):
                self.assertFalse(self.parse_trade_with("price", value)["ok"])

    def test_rejects_timestamp_that_overflows_millisecond_conversion(self):
        self.assertEqual(self.parse_trade_with("timestamp", "1e308")["reason"],
                         "invalid_timestamp")

    def test_rejects_invalid_utf8_bytes_without_raising(self):
        result = parse_trade_message(b'{"trade":\xff}')
        self.assertEqual(result, {"ok": False, "reason": "invalid_utf8"})

    def test_rejects_oversized_bytes_before_decode(self):
        result = parse_trade_message(b"x" * (MAX_MESSAGE_BYTES + 1))
        self.assertEqual(result, {"ok": False, "reason": "message_too_large"})

    def test_rejects_oversized_text(self):
        result = parse_trade_message(" " * (MAX_MESSAGE_BYTES + 1))
        self.assertEqual(result, {"ok": False, "reason": "message_too_large"})

    def test_rejects_lone_surrogate_text_without_raising(self):
        result = parse_trade_message('{"trade":"\\ud800"}')
        self.assertEqual(result, {"ok": False, "reason": "invalid_utf8"})

    def test_rejects_non_string_message_types(self):
        for message in (None, 123, 1.5, {}, [], bytearray(b"{}")):
            with self.subTest(message_type=type(message).__name__):
                self.assertEqual(parse_trade_message(message)["reason"],
                                 "invalid_or_oversized_message")

    def test_rejects_invalid_json_and_non_object_json(self):
        for message in ("", "not json", "{", "null", "[]", '"text"', "123"):
            with self.subTest(message=message):
                result = parse_trade_message(message)
                self.assertFalse(result["ok"])

    def test_rejects_invalid_envelopes_and_symbol_mismatch(self):
        self.assertEqual(parse_trade_message('{"error":"Invalid params"}')["reason"],
                         "exchange_error")
        self.assertEqual(parse_trade_message('{"trade":[]}')["reason"],
                         "missing_trade_object")
        other = {"trade": {"symbol": "BTC_USDT", "price": "1",
                           "quantity": "1", "timestamp": "1700000000"}}
        self.assertEqual(parse_trade_message(json.dumps(other), "AEVO_USDT")["reason"],
                         "symbol_mismatch")


if __name__ == "__main__":
    unittest.main()
