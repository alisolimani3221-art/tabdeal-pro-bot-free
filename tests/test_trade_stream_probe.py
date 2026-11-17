import json
import sys
import types
import unittest
from unittest.mock import patch

from trade_stream_probe import inspect_trade_message, probe_broadcast


class TradeStreamProbeTests(unittest.TestCase):
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

    def test_real_trade_field_profile_is_redacted_and_classified(self):
        payload = {"trade": {"price": "123.4500", "amount": "0.250",
                             "sequence": 12345, "side_name": "BUY",
                             "symbol": "BTC_USDT",
                             "updated": "2026-11-17T00:35:14.123Z"}}
        result = inspect_trade_message(json.dumps(payload),
                                       expected_symbol="BTC_USDT")
        self.assertTrue(result["ok"])
        profile = result["trade_profile"]
        self.assertTrue(profile["price"]["valid_decimal_string"])
        self.assertEqual(profile["price"]["decimal_places"], 4)
        self.assertTrue(profile["amount"]["positive"])
        self.assertTrue(profile["sequence"]["integer"])
        self.assertEqual(profile["side_name"]["classification"], "buy_like")
        self.assertTrue(profile["symbol"]["matches_requested"])
        self.assertEqual(profile["updated"]["format"], "iso8601_datetime")
        self.assertTrue(profile["updated"]["timezone_aware"])
        rendered = json.dumps(result)
        for secret in ("123.4500", "0.250", "12345", "BTC_USDT",
                       "2026-11-17T00:35:14.123Z", "BUY"):
            self.assertNotIn(secret, rendered)

    def test_timestamp_classifier_recognizes_epoch_milliseconds_shape(self):
        payload = {"trade": {"updated": "1700000000123"}}
        result = inspect_trade_message(json.dumps(payload))
        self.assertEqual(result["trade_profile"]["updated"]["format"],
                         "epoch_milliseconds_string")

    def test_schema_inspector_rejects_unknown_or_invalid_envelopes(self):
        self.assertEqual(inspect_trade_message('{"trade": null}')["reason"],
                         "trade_not_object")
        self.assertEqual(inspect_trade_message("not json")["reason"], "invalid_json")

    def test_schema_inspector_rejects_invalid_unicode(self):
        result = inspect_trade_message('{"trade": "\ud800"}')
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_unicode")

    def test_schema_inspector_rejects_error_envelope(self):
        result = inspect_trade_message('{"error": "rejected"}')
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "exchange_error")

    def test_schema_inspector_does_not_count_non_object_trade(self):
        result = inspect_trade_message('{"trade": null}')
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "trade_not_object")
        self.assertNotIn("trade_shape", result)

    def test_empty_trade_object_is_not_a_trade_observation(self):
        result = inspect_trade_message('{"trade": {}}')
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "empty_trade_object")
        self.assertNotIn("trade_shape", result)

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


if __name__ == "__main__":
    unittest.main()
