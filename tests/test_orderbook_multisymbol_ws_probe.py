import json
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
            "params": ["BTC_USDT@depth@2000ms", "ETH_USDT@depth@2000ms"],
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
        self.assertEqual(_find_symbol({"stream": "ETH_USDT@depth@2000ms"}), "ETH_USDT")
        self.assertEqual(_find_symbol({"data": {"s": "BTC_USDT"}}), "BTC_USDT")
        self.assertIsNone(_find_symbol({"bids": [["1", "2"]], "asks": [["2", "3"]]}))

    def test_same_connection_receives_two_symbol_books(self):
        state = {"sent": [], "closed": False}
        class FakeWebSocketApp:
            def __init__(self, url, **callbacks):
                state["url"] = url
                self.callbacks = callbacks
            def send(self, payload):
                state["sent"].append(json.loads(payload))
            def close(self):
                state["closed"] = True
            def run_forever(self, **kwargs):
                self.callbacks["on_open"](self)
                for symbol, bid, ask in (("BTC_USDT", "90000", "90001"),
                                          ("ETH_USDT", "3000", "3001")):
                    frame = {"stream": symbol + "@depth@2000ms",
                             "data": {"bids": [[bid, "1"]], "asks": [[ask, "2"]]}}
                    self.callbacks["on_message"](self, json.dumps(frame))
        module = types.SimpleNamespace(
            WebSocketApp=FakeWebSocketApp,
            getdefaulttimeout=lambda: None,
            setdefaulttimeout=lambda value: None,
        )
        with patch.dict(sys.modules, {"websocket": module}):
            result = probe_multi_market_order_book(timeout_seconds=1)
        self.assertEqual(len(state["sent"]), 1)
        self.assertEqual(state["sent"][0]["params"],
                         ["BTC_USDT@depth@2000ms", "ETH_USDT@depth@2000ms"])
        self.assertTrue(state["closed"])
        self.assertEqual(result["status"], "both_symbol_books_observed")
        self.assertEqual(result["valid_books_by_symbol"], {"BTC_USDT": 1, "ETH_USDT": 1})


if __name__ == "__main__":
    unittest.main()
