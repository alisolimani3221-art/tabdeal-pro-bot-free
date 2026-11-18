import unittest

from live_execution_guard import evaluate_entry, authorize_close


class LiveExecutionGuardTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "verified": True,
            "source": "verified_trade_stream",
            "symbol": "BTC_USDT",
            "observed_at": 198.0,
            "sequence_gap": False,
            "reconnected": False,
        }
        self.technical = {"ready": True, "signal": "BULLISH"}
        self.account = {"positions": [], "daily_loss_percent": 0.0}
        self.kwargs = dict(
            live_enabled=True,
            explicit_confirmation=True,
            market_snapshot=self.snapshot,
            technical=self.technical,
            account=self.account,
            capital_usdt=1000,
            entry_price=65000,
            stop_percent=1,
            reward_risk=2,
            now=200.0,
        )

    def test_live_disabled_is_always_blocked(self):
        result = evaluate_entry(**dict(self.kwargs, live_enabled=False))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "live_trading_disabled")

    def test_per_order_confirmation_is_required(self):
        result = evaluate_entry(**dict(self.kwargs, explicit_confirmation=False))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "per_order_confirmation_required")

    def test_order_book_midpoint_cannot_authorize_entry(self):
        snapshot = dict(self.snapshot, source="order_book_midpoint")
        result = evaluate_entry(**dict(self.kwargs, market_snapshot=snapshot))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "order_book_midpoint_is_not_trade_data")

    def test_unverified_or_stale_data_is_blocked(self):
        for snapshot, expected in (
            (dict(self.snapshot, verified=False), "market_data_not_verified"),
            (dict(self.snapshot, observed_at=190.0), "market_data_stale"),
            (dict(self.snapshot, sequence_gap=True), "market_stream_gap_or_reconnect"),
        ):
            with self.subTest(expected=expected):
                result = evaluate_entry(**dict(self.kwargs, market_snapshot=snapshot))
                self.assertFalse(result["allowed"])
                self.assertEqual(result["reason"], expected)

    def test_no_signal_or_incomplete_warmup_is_blocked(self):
        for technical, expected in (
            ({"ready": False, "signal": "BULLISH"}, "technical_warmup_incomplete"),
            ({"ready": True, "signal": "NEUTRAL"}, "no_trade_signal"),
        ):
            with self.subTest(expected=expected):
                result = evaluate_entry(**dict(self.kwargs, technical=technical))
                self.assertFalse(result["allowed"])
                self.assertEqual(result["reason"], expected)

    def test_one_open_position_blocks_second_entry(self):
        account = {"positions": [{"symbol": "ETH_USDT"}], "daily_loss_percent": 0}
        result = evaluate_entry(**dict(self.kwargs, account=account))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "maximum_open_positions_reached")

    def test_daily_loss_stop_blocks_new_entries(self):
        account = {"positions": [], "daily_loss_percent": 1.0}
        result = evaluate_entry(**dict(self.kwargs, account=account))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "daily_loss_limit_reached")

    def test_valid_proposal_is_capped_and_never_submits_order(self):
        result = evaluate_entry(**self.kwargs)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["mode"], "proposal_only_no_order_sent")
        self.assertEqual(result["leverage"], 1)
        self.assertEqual(result["risk_percent"], 0.25)
        self.assertLessEqual(result["risk_amount"], 2.500001)
        self.assertTrue(result["requires_exchange_side_sl_tp"])

    def test_invalid_risk_inputs_are_blocked(self):
        result = evaluate_entry(**dict(self.kwargs, stop_percent=0))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "invalid_or_unsafe_risk_inputs")

    def test_close_requires_confirmation_and_existing_position(self):
        positions = [{"symbol": "BTC_USDT"}]
        self.assertFalse(authorize_close(
            live_enabled=False, explicit_confirmation=True,
            symbol="BTC_USDT", positions=positions
        )["allowed"])
        self.assertFalse(authorize_close(
            live_enabled=True, explicit_confirmation=False,
            symbol="BTC_USDT", positions=positions
        )["allowed"])
        self.assertFalse(authorize_close(
            live_enabled=True, explicit_confirmation=True,
            symbol="BTC_USDT", positions=[]
        )["allowed"])
        allowed = authorize_close(
            live_enabled=True, explicit_confirmation=True,
            symbol="BTC_USDT", positions=positions
        )
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["mode"], "close_proposal_only_no_order_sent")


if __name__ == "__main__":
    unittest.main()
