import unittest
from unittest.mock import Mock

from tabdeal_trade import TabdealFuturesTradeClient


class TradeAdapterSafetyTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = TabdealFuturesTradeClient(
            "test-key", "test-secret", session=self.session
        )

    def test_get_positions_uses_official_signed_read_gateway(self):
        from urllib.parse import parse_qs, urlparse
        import hashlib
        import hmac

        response = Mock()
        response.ok = True
        response.json.return_value = [{"positionId": 42, "symbol": "BTC_USDT"}]
        self.session.get.return_value = response
        result = self.client.get_positions("BTC_USDT")
        self.assertEqual(result[0]["positionId"], 42)
        args, kwargs = self.session.get.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/r/fapi/v1/position")
        query = parse_qs(urlparse(args[0] + "?" + __import__("urllib.parse").parse.urlencode(kwargs["params"])).query)
        signature = query.pop("signature")[0]
        signed_query = __import__("urllib.parse").parse.urlencode({
            key: value[0] for key, value in query.items()
        })
        expected = hmac.new(b"test-secret", signed_query.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)
        self.assertEqual(kwargs["params"]["symbol"], "BTC_USDT")
        self.assertEqual(kwargs["headers"]["X-MBX-APIKEY"], "test-key")
        response.close.assert_called_once()

    def test_get_positions_rejects_invalid_symbol_before_network(self):
        with self.assertRaises(ValueError):
            self.client.get_positions("BTCUSDT")
        self.session.get.assert_not_called()

    def test_live_order_is_disabled_by_default_without_network(self):
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            self.client.place_market_order(
                "BTC_USDT", "BUY", "0.001", confirm_live=True
            )
        self.session.post.assert_not_called()

    def test_live_order_requires_per_call_confirmation(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        with self.assertRaisesRegex(RuntimeError, "confirm"):
            client.place_market_order("BTC_USDT", "BUY", "0.001")
        self.session.post.assert_not_called()

    def test_invalid_order_inputs_never_reach_network(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        invalid = [
            ("BTCUSDT", "BUY", "0.001"),
            ("BTC_USDT", "SIDEWAYS", "0.001"),
            ("BTC_USDT", "BUY", 0),
            ("BTC_USDT", "BUY", float("nan")),
        ]
        for symbol, side, quantity in invalid:
            with self.subTest(symbol=symbol, side=side, quantity=quantity):
                with self.assertRaises(ValueError):
                    client.place_market_order(
                        symbol, side, quantity, confirm_live=True
                    )
        self.session.post.assert_not_called()

    def test_risk_increasing_market_entry_is_blocked_before_network(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        with self.assertRaisesRegex(RuntimeError, "Risk-increasing market entries are blocked"):
            client.place_market_order(
                "BTC_USDT", "BUY", "0.001", confirm_live=True
            )
        self.session.post.assert_not_called()

    def test_valid_order_uses_official_futures_order_endpoint(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        response = Mock()
        response.ok = True
        response.json.return_value = {"orderId": 123}
        self.session.post.return_value = response
        result = client.place_market_order(
            "BTC_USDT", "BUY", "0.001", reduce_only=True,
            confirm_live=True
        )
        self.assertEqual(result["orderId"], 123)
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/fapi/v1/order")
        self.assertEqual(kwargs["data"]["type"], "MARKET")
        self.assertEqual(kwargs["data"]["symbol"], "BTC_USDT")
        self.assertEqual(kwargs["data"]["reduceOnly"], "true")
        self.assertIn("signature", kwargs["data"])
        self.assertEqual(kwargs["headers"]["X-MBX-APIKEY"], "test-key")
        response.close.assert_called_once()

    def test_sl_tp_requires_at_least_one_valid_price(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        with self.assertRaises(ValueError):
            client.set_position_sl_tp(
                "BTC_USDT", 42, confirm_live=True
            )
        with self.assertRaises(ValueError):
            client.set_position_sl_tp(
                "BTC_USDT", 42, sl_price=-1, confirm_live=True
            )
        self.session.post.assert_not_called()

    def test_sl_tp_uses_documented_endpoint_and_parameters(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        response = Mock()
        response.ok = True
        response.json.return_value = {"ok": True}
        self.session.post.return_value = response
        client.set_position_sl_tp(
            "BTC_USDT", 42, sl_price="65000", tp_price="68000",
            confirm_live=True
        )
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/fapi/v1/positionSlTp")
        self.assertEqual(kwargs["data"]["positionId"], 42)
        self.assertEqual(kwargs["data"]["slPrice"], "65000")
        self.assertEqual(kwargs["data"]["tpPrice"], "68000")

    def test_close_position_is_disabled_without_confirmation(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        with self.assertRaises(RuntimeError):
            client.close_position("BTC_USDT")
        self.session.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
