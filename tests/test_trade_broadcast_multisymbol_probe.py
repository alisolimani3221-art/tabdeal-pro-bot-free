import json
import time
import unittest
from unittest.mock import patch
import sys
import types

from trade_broadcast_multisymbol_probe import probe_multiple_symbols


class MultiSymbolBroadcastProbeTests(unittest.TestCase):
    def _fake_websocket(self, mode):
        state = {"sent": [], "closed": False}
        class FakeWebSocketApp:
            def __init__(self, url, **callbacks):
                state["url"] = url
                self.callbacks = callbacks
            def send(self, payload):
                state["sent"].append(payload)
            def close(self):
                state["closed"] = True
            def run_forever(self, **kwargs):
                self.callbacks["on_open"](self)
                first = json.dumps({"trade": {"symbol": "BTC_USDT", "price": "90000",
                    "quantity": "0.01", "timestamp": time.time()}})
                second = json.dumps({"trade": {"symbol": "ETH_USDT", "price": "3000",
                    "quantity": "0.1", "timestamp": time.time()}})
                self.callbacks["on_message"](self, first)
                if mode in ("both", "sustained"):
                    self.callbacks["on_message"](self, second)
                    self.callbacks["on_message"](self, first)
                    if mode == "sustained":
                        self.callbacks["on_message"](self, second)
                        self.callbacks["on_message"](self, first)
                        self.callbacks["on_message"](self, second)
                        self.callbacks["on_message"](self, first)
                elif mode == "second_only":
                    self.callbacks["on_message"](self, second)
        module = types.SimpleNamespace(
            WebSocketApp=FakeWebSocketApp,
            getdefaulttimeout=lambda: None,
            setdefaulttimeout=lambda value: None,
        )
        return state, module

    def test_one_connection_observes_both_symbols_after_second_subscription(self):
        state, module = self._fake_websocket("both")
        with patch.dict(sys.modules, {"websocket": module}):
            result = probe_multiple_symbols(timeout_seconds=1)
        self.assertEqual(state["sent"], ["BTC_USDT", "ETH_USDT"])
        self.assertEqual(result["status"], "both_symbols_observed_briefly_or_insufficiently")
        self.assertGreater(result["post_second_subscription_by_symbol"]["BTC_USDT"], 0)
        self.assertGreater(result["post_second_subscription_by_symbol"]["ETH_USDT"], 0)
        self.assertFalse(state["closed"], "probe must not close immediately after one event per symbol")

    def test_requires_sustained_window_and_repeated_events_for_success(self):
        state, module = self._fake_websocket("sustained")
        with patch.dict(sys.modules, {"websocket": module}), patch(
            "trade_broadcast_multisymbol_probe.time.monotonic", side_effect=[0.0, 35.0]
        ):
            result = probe_multiple_symbols(timeout_seconds=60)
        self.assertEqual(result["status"], "both_symbols_sustained_after_second_subscription")
        self.assertGreaterEqual(result["post_second_subscription_by_symbol"]["BTC_USDT"], 3)
        self.assertGreaterEqual(result["post_second_subscription_by_symbol"]["ETH_USDT"], 3)
        self.assertFalse(state["closed"])

    def test_reports_replacement_or_single_symbol_behavior_without_guessing(self):
        state, module = self._fake_websocket("second_only")
        with patch.dict(sys.modules, {"websocket": module}):
            result = probe_multiple_symbols(timeout_seconds=1)
        self.assertEqual(state["sent"], ["BTC_USDT", "ETH_USDT"])
        self.assertEqual(result["status"], "second_symbol_only_after_second_subscription")

    def test_rejects_invalid_symbols_and_timeouts_before_network(self):
        for symbols in (("BTC_USDT",), ("BTC_USDT", "BTC_USDT"),
                        ("BTC/USDT", "ETH_USDT"), ("BTC_USDT", 3)):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                probe_multiple_symbols(symbols=symbols)
        for timeout in (True, 0, -1, float("inf"), 181, "bad"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                probe_multiple_symbols(timeout_seconds=timeout)


if __name__ == "__main__":
    unittest.main()
