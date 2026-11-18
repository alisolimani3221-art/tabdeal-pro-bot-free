import unittest

from trade_preflight import validate_order_preflight


class TradePreflightTests(unittest.TestCase):
    def setUp(self):
        self.info = {
            "symbol": "BTC_USDT",
            "status": "TRADING",
            "quoteAsset": "USDT",
            "contractType": "PERPETUAL",
            "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.001",
                 "maxQty": "100", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
            ],
        }

    def validate(self, **kwargs):
        args = dict(symbol="BTC_USDT", side="BUY", quantity="0.001",
                    reference_price="50000", symbol_info=self.info)
        args.update(kwargs)
        return validate_order_preflight(**args)

    def test_valid_plan_is_preflight_only(self):
        result = self.validate()
        self.assertTrue(result["preflight_only"])
        self.assertFalse(result["live_order_submitted"])
        self.assertEqual(result["notional_usdt"], "50.000")

    def test_rejects_wrong_symbol_metadata(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.validate(symbol_info=dict(self.info, symbol="ETH_USDT"))

    def test_rejects_disabled_symbol(self):
        with self.assertRaisesRegex(ValueError, "not enabled"):
            self.validate(symbol_info=dict(self.info, status="BREAK"))

    def test_rejects_quantity_step_mismatch(self):
        with self.assertRaisesRegex(ValueError, "step size"):
            self.validate(quantity="0.0015")

    def test_rejects_below_minimum_notional(self):
        with self.assertRaisesRegex(ValueError, "below exchange minimum"):
            self.validate(quantity="0.001", reference_price="1000")

    def test_rejects_duplicate_symbol_position(self):
        with self.assertRaisesRegex(ValueError, "already open"):
            self.validate(open_positions=[{"symbol": "BTC_USDT", "positionAmt": "0.01"}])

    def test_rejects_global_position_limit(self):
        with self.assertRaisesRegex(ValueError, "limit reached"):
            self.validate(open_positions=[{"symbol": "ETH_USDT", "positionAmt": "1"}])

    def test_rejects_notional_cap(self):
        with self.assertRaisesRegex(ValueError, "notional cap"):
            self.validate(max_notional_usdt="20")


if __name__ == "__main__":
    unittest.main()
