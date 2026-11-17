import json
import sys
import types
import unittest
from unittest.mock import patch

from trade_stream_probe import (inspect_trade_message, parse_trade_message,
                                probe_broadcast)


class TradeStreamProbeTests(unittest.TestCase):
    def test_documented_trade_envelope_with_common_fields(self):
        payload = {"trade": {"symbol": "AEVO_USDT", "price": "1.25",
                             "quantity": "4", "timestamp": 1700000000}}
        result = parse_trade_message(json.dumps(payload), "AEVO_USDT")
        self.assertTrue(result["ok"])
        self.assertEqual(result["price"], 1.25)
        self.assertEqual(result["quantity"], 4.0)
        self.assertEqual(result["timestamp_ms"], 1700000000000)

    def test_schema_inspector_reports_keys_and_types_without_values(self):
        payload = {"trade": {"symbol": "BTC_USDT", "px": "123.45",
                             "size": "0.5", "event_time": 1700000000123}}
        result = inspect_trade_message(json.dumps(payload))
        self.assertTrue(result["ok"])
        shape = result["trade_shape"]
        self.assertEqual(shape["type"], "object")
        self.assertEqual(shape["keys"]["px"]["type"], "string")
        self.assertEqual(shape["keys"]["event_time"]["type"], "number")
        self.assertNotIn("123.45", json.dumps(result))
        self.assertNotIn("BTC_USDT", json.dumps(result))

    def test_schema_inspector_rejects_unknown_or_invalid_envelopes(self):
        self.assertEqual(inspect_trade_message('{"trade": null}')["reason"],
                         "trade_not_object")
        self.assertEqual(inspect_trade_message("not json")["reason"], "invalid_json")

    def test_schema_inspector_does_not_count_non_object_trade(self):
        result = inspect_trade_message('{"trade": null}')
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "trade_not_object")
        self.assertNotIn("trade_shape", result)

    def test_empty_trade_object_is_observed_but_not_schema_confirmed(self):
        result = inspect_trade_message('{"trade": {}}')
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "trade_object_observed")
        self.assertEqual(result["trade_shape"], {"type": "object", "keys": {}})

    def test_probe_does_not_count_null_trade_as_observed(self):
        class FakeWebSocketApp:
            def __init__(self, url, on_open, on_message, on_error):
                self.on_open = on_open
                self.on_message = on_message
            def run_forever(self, **kwargs):
                self.on_open(self)
                self.on_message(self, '{"trade": null}')
            def send(self, value):
                self.sent = value
            def close(self):
                pass

        fake_module = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
        with patch.dict(sys.modules, {"websocket": fake_module}):
            report = probe_broadcast(timeout_seconds=2, max_messages=1)
        self.assertTrue(report["connected"])
        self.assertEqual(report["messages"], 1)
        self.assertEqual(report["trade_envelopes"], 0)
        self.assertEqual(report["probe_status"], "connected_no_trade_object")
        self.assertFalse(report["schema_confirmed"])

    def test_probe_records_object_but_never_confirms_schema(self):
        class FakeWebSocketApp:
            def __init__(self, url, on_open, on_message, on_error):
                self.on_open = on_open
                self.on_message = on_message
            def run_forever(self, **kwargs):
                self.on_open(self)
                self.on_message(self, '{"trade": {"field": "secret-value"}}')
            def send(self, value):
                self.sent = value
            def close(self):
                pass

        fake_module = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
        with patch.dict(sys.modules, {"websocket": fake_module}):
            report = probe_broadcast(timeout_seconds=2, max_messages=1)
        self.assertEqual(report["trade_envelopes"], 1)
        self.assertEqual(report["probe_status"], "observed_trade_object")
        self.assertFalse(report["schema_confirmed"])
        self.assertNotIn("secret-value", json.dumps(report))

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
