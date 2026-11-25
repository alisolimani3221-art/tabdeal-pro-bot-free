import json
import time
import unittest
import sys
import types
from unittest.mock import patch

from orderbook_multisymbol_ws_probe import (
    build_multi_depth_subscription, _find_symbol, probe_multi_market_order_book,
)


class MultiMarketOrderBookWebSocketProbeTests(unittest.TestCase):
    def test_builds_exact_official_sdk_subscription_payload(self):
        self.assertEqual(build_multi_depth_subscription(("BTC_USDT", "ETH_USDT")), {
            "method": "SUBSCRIBE", "id": 1,
            "params": ["btcusdt@depth@2000ms", "ethusdt@depth@2000ms"],
        })

    def test_rejects_invalid_or_duplicate_symbols_and_request_ids(self):
        for symbols in ((), ("BTC_USDT", "BTC_USDT"), ("BTC/USDT",), ("BTC_USDT", 4),
                        tuple("A{}_USDT".format(i) for i in range(21))):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                build_multi_depth_subscription(symbols)
        for request_id in (True, 0, -1, "1"):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                build_multi_depth_subscription(("BTC_USDT",), request_id)

    def test_symbol_extraction_requires_explicit_identifier(self):
        self.assertEqual(_find_symbol({"stream": "ethusdt@depth@2000ms"}), "ETH_USDT")
        self.assertEqual(_find_symbol({"data": {"s": "btcusdt"}}), "BTC_USDT")
        self.assertEqual(_find_symbol({"symbol": "ETH_USDT"}), "ETH_USDT")
        self.assertIsNone(_find_symbol({"bids": [["1", "2"]], "asks": [["2", "3"]]}))

    def test_single_baseline_then_combined_subscription_observe_both_symbols(self):
        state = {"sent": [], "closed": 0, "connections": 0}
        class FakeWebSocketApp:
            def __init__(self, url, **callbacks):
                state["url"] = url
                state["connections"] += 1
                self.callbacks = callbacks
            def send(self, payload):
                state["sent"].append(json.loads(payload))
            def close(self):
                state["closed"] += 1
            def run_forever(self, **kwargs):
                self.callbacks["on_open"](self)
                payload = state["sent"][-1]
                for stream in payload["params"]:
                    symbol = stream.split("@", 1)[0]
                    base = 90000 if symbol.startswith("btc") else 3000
                    frame = {
                        "stream": stream,
                        "data": {"s": symbol, "bids": [[str(base), "1"]],
                                 "asks": [[str(base + 1), "2"]]},
                    }
                    self.callbacks["on_message"](self, json.dumps(frame))
        module = types.SimpleNamespace(
            WebSocketApp=FakeWebSocketApp,
            getdefaulttimeout=lambda: None,
            setdefaulttimeout=lambda value: None,
        )
        with patch.dict(sys.modules, {"websocket": module}):
            result = probe_multi_market_order_book(timeout_seconds=3)
        self.assertEqual(state["connections"], 2)
        self.assertEqual(state["sent"][0]["params"], ["btcusdt@depth@2000ms"])
        self.assertEqual(state["sent"][1]["params"],
                         ["btcusdt@depth@2000ms", "ethusdt@depth@2000ms"])
        self.assertEqual(result["status"], "both_symbol_books_observed")
        self.assertEqual(result["valid_books_by_symbol"], {"BTC_USDT": 1, "ETH_USDT": 1})

    def test_rejects_bad_timeout(self):
        for timeout in (True, 0, 1, -1, float("inf"), 181, "bad"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                probe_multi_market_order_book(timeout_seconds=timeout)


if __name__ == "__main__":
    unittest.main()
