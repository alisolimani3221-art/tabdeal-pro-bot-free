import unittest
from unittest.mock import Mock

from tabdeal_trade import TabdealFuturesTradeClient


class TradeAdapterSafetyTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = TabdealFuturesTradeClient(
            "test-key", "test-secret", session=self.session
        )

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
            "BTC_USDT", "BUY", "0.001", confirm_live=True
        )
        self.assertEqual(result["orderId"], 123)
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/fapi/v1/order")
        self.assertEqual(kwargs["data"]["type"], "MARKET")
        self.assertEqual(kwargs["data"]["symbol"], "BTC_USDT")
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
