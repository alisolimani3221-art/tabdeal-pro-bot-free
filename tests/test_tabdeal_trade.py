import unittest
from unittest.mock import Mock

from tabdeal_trade import TabdealFuturesTradeClient


class TradeAdapterSafetyTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = TabdealFuturesTradeClient(
            "test-key", "test-secret", session=self.session
        )

    def test_signed_request_rejects_unsupported_method_endpoint_pairs(self):
        invalid_pairs = [
            ("POST", "/v1/position"),
            ("DELETE", "/v1/order"),
            ("POST", "/v1/openOrders"),
            ("PUT", "/v1/order"),
        ]
        for method, path in invalid_pairs:
            with self.subTest(method=method, path=path):
                with self.assertRaisesRegex(ValueError, "method/endpoint"):
                    self.client._signed_request(method, path, {})
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()
        self.session.delete.assert_not_called()

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

    def test_get_open_orders_uses_read_only_gateway(self):
        response = Mock()
        response.ok = True
        response.json.return_value = [{"orderId": 8, "type": "STOP_MARKET"}]
        self.session.get.return_value = response
        result = self.client.get_open_orders("BTC_USDT")
        self.assertEqual(result[0]["orderId"], 8)
        args, kwargs = self.session.get.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/r/fapi/v1/openOrders")
        self.assertEqual(kwargs["params"]["symbol"], "BTC_USDT")
        self.assertEqual(kwargs["headers"]["X-MBX-APIKEY"], "test-key")
        self.assertIn("signature", kwargs["params"])
        response.close.assert_called_once()

    def test_get_order_requires_positive_integer_id(self):
        with self.assertRaises(ValueError):
            self.client.get_order("BTC_USDT", 0)
        with self.assertRaises(ValueError):
            self.client.get_order("BTC_USDT", True)
        self.session.get.assert_not_called()

    def test_get_order_uses_signed_read_gateway(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {"orderId": 9, "status": "NEW"}
        self.session.get.return_value = response
        result = self.client.get_order("BTC_USDT", 9)
        self.assertEqual(result["orderId"], 9)
        args, kwargs = self.session.get.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/r/fapi/v1/order")
        self.assertEqual(kwargs["params"]["orderId"], 9)
        self.assertIn("signature", kwargs["params"])
        response.close.assert_called_once()

    def test_get_position_risk_uses_documented_v3_read_gateway(self):
        response = Mock()
        response.ok = True
        response.json.return_value = [{"symbol": "BTC_USDT"}]
        self.session.get.return_value = response
        result = self.client.get_position_risk("BTC_USDT")
        self.assertEqual(result[0]["symbol"], "BTC_USDT")
        args, kwargs = self.session.get.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/r/fapi/v3/positionRisk")
        self.assertEqual(kwargs["params"]["symbol"], "BTC_USDT")
        self.assertIn("signature", kwargs["params"])
        response.close.assert_called_once()

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

    def test_financial_values_preserve_decimal_precision(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        response = Mock()
        response.ok = True
        response.json.return_value = {"orderId": 321}
        self.session.post.return_value = response
        client.place_market_order(
            "BTC_USDT", "SELL", "0.000000000000000123",
            reduce_only=True, confirm_live=True
        )
        self.assertEqual(
            self.session.post.call_args.kwargs["data"]["quantity"],
            "0.000000000000000123"
        )

        self.session.post.reset_mock()
        client.set_position_sl_tp(
            "BTC_USDT", 42,
            sl_price="65000.000000000000123",
            tp_price="68000.000000000000456",
            position_side="LONG", reference_price="66000",
            confirm_live=True
        )
        params = self.session.post.call_args.kwargs["data"]
        self.assertEqual(params["slPrice"], "65000.000000000000123")
        self.assertEqual(params["tpPrice"], "68000.000000000000456")

    def test_decimal_parser_rejects_non_finite_and_boolean_values(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        for value in (True, "NaN", "Infinity", "-Infinity", "0", "-1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    client.set_position_sl_tp(
                        "BTC_USDT", 42, sl_price=value, tp_price="68000",
                        position_side="LONG", reference_price="66000",
                        confirm_live=True
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

    def test_sl_tp_requires_both_valid_protective_prices(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        with self.assertRaisesRegex(ValueError, "both sl_price and tp_price"):
            client.set_position_sl_tp(
                "BTC_USDT", 42, sl_price="65000", position_side="LONG",
                reference_price="66000", confirm_live=True
            )
        with self.assertRaisesRegex(ValueError, "both sl_price and tp_price"):
            client.set_position_sl_tp(
                "BTC_USDT", 42, tp_price="68000", position_side="LONG",
                reference_price="66000", confirm_live=True
            )
        with self.assertRaises(ValueError):
            client.set_position_sl_tp(
                "BTC_USDT", 42, sl_price=-1, tp_price="68000",
                position_side="LONG", reference_price="66000",
                confirm_live=True
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
            position_side="LONG", reference_price="66000",
            confirm_live=True
        )
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://api1.tabdeal.org/fapi/v1/positionSlTp")
        self.assertEqual(kwargs["data"]["positionId"], 42)
        self.assertEqual(kwargs["data"]["slPrice"], "65000")
        self.assertEqual(kwargs["data"]["tpPrice"], "68000")

    def test_sl_tp_requires_direction_and_reference_price(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        with self.assertRaisesRegex(ValueError, "position_side"):
            client.set_position_sl_tp(
                "BTC_USDT", 42, sl_price="65000", tp_price="68000",
                reference_price="66000", confirm_live=True
            )
        with self.assertRaisesRegex(ValueError, "reference_price"):
            client.set_position_sl_tp(
                "BTC_USDT", 42, sl_price="65000", tp_price="68000",
                position_side="LONG", confirm_live=True
            )
        self.session.post.assert_not_called()

    def test_sl_tp_rejects_levels_on_wrong_side_of_reference(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        invalid_cases = [
            ("LONG", "67000", "68000", "66000"),
            ("LONG", "65000", "65500", "66000"),
            ("SHORT", "65000", "64000", "66000"),
            ("SHORT", "67000", "68000", "66000"),
        ]
        for side, sl, tp, reference in invalid_cases:
            with self.subTest(side=side, sl=sl, tp=tp):
                with self.assertRaisesRegex(ValueError, "requires"):
                    client.set_position_sl_tp(
                        "BTC_USDT", 42, sl_price=sl, tp_price=tp,
                        position_side=side, reference_price=reference,
                        confirm_live=True
                    )
        self.session.post.assert_not_called()

    def test_short_sl_tp_accepts_correct_bracketing(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        response = Mock()
        response.ok = True
        response.json.return_value = {"code": 0, "msg": "accepted"}
        self.session.post.return_value = response
        client.set_position_sl_tp(
            "BTC_USDT", 42, sl_price="68000", tp_price="64000",
            position_side="SHORT", reference_price="66000",
            confirm_live=True
        )
        params = self.session.post.call_args.kwargs["data"]
        self.assertEqual(params["slPrice"], "68000")
        self.assertEqual(params["tpPrice"], "64000")

    def test_close_position_is_disabled_because_endpoint_closes_all_symbol_positions(self):
        client = TabdealFuturesTradeClient(
            "test-key", "test-secret", live_enabled=True,
            session=self.session
        )
        with self.assertRaisesRegex(RuntimeError, "closes all open positions for the symbol"):
            client.close_position("BTC_USDT", confirm_live=True)
        self.session.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
