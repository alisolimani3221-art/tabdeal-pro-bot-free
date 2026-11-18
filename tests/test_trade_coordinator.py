import copy
import unittest

from trade_coordinator import TradeCoordinator


class TradeCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.info = {
            "symbol": "BTC_USDT", "status": "TRADING",
            "quoteAsset": "USDT", "contractType": "PERPETUAL",
            "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.001",
                 "maxQty": "10", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
            ],
        }
        self.coordinator = TradeCoordinator(max_open_positions=1, max_notional_usdt="100")

    def prepare(self, **kwargs):
        args = dict(
            idempotency_key="intent-1", symbol="BTC_USDT", side="BUY",
            quantity="0.001", reference_price="50000",
            symbol_info=self.info, open_positions=[],
            stop_loss="49000", take_profit="53000",
            account_snapshot_age_seconds=1,
        )
        args.update(kwargs)
        return self.coordinator.prepare_entry(**args)

    def test_prepare_is_local_only_and_includes_protection(self):
        intent = self.prepare()
        self.assertEqual(intent["state"], "PREPARED_ONLY")
        self.assertFalse(intent["live_order_submitted"])
        self.assertFalse(intent["execution_enabled"])
        self.assertEqual(intent["stop_loss"], "49000")

    def test_duplicate_idempotency_key_rejected(self):
        self.prepare()
        with self.assertRaisesRegex(RuntimeError, "duplicate idempotency"):
            self.prepare()

    def test_pending_same_symbol_rejected(self):
        self.prepare()
        with self.assertRaisesRegex(RuntimeError, "pending intent"):
            self.prepare(idempotency_key="intent-2")

    def test_stale_account_snapshot_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "stale"):
            self.prepare(account_snapshot_age_seconds=11)

    def test_missing_protective_order_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "both protective"):
            self.prepare(take_profit=None)

    def test_wrong_buy_stop_target_orientation_rejected(self):
        with self.assertRaisesRegex(ValueError, "BUY stop/target"):
            self.prepare(stop_loss="51000")

    def test_sell_stop_target_orientation_rejected(self):
        with self.assertRaisesRegex(ValueError, "SELL stop/target"):
            self.prepare(side="SELL", stop_loss="49000", take_profit="51000")

    def test_emergency_stop_blocks_new_intents(self):
        result = self.coordinator.emergency_stop("test")
        self.assertTrue(result["halted"])
        with self.assertRaisesRegex(RuntimeError, "halted"):
            self.prepare()

    def test_prepared_intent_survives_restart_without_enabling_execution(self):
        self.prepare()
        restored = TradeCoordinator.restore_state(self.coordinator.export_state())
        self.assertFalse(restored.halted)
        self.assertFalse(restored.export_state()["intents"]["intent-1"]["live_order_submitted"])
        with self.assertRaisesRegex(RuntimeError, "duplicate idempotency"):
            restored.prepare_entry(
                idempotency_key="intent-1", symbol="BTC_USDT", side="BUY",
                quantity="0.001", reference_price="50000",
                symbol_info=self.info, open_positions=[],
                stop_loss="49000", take_profit="53000",
                account_snapshot_age_seconds=1)

    def test_restart_with_ambiguous_submitted_state_halts(self):
        state = self.coordinator.export_state()
        state["intents"]["ambiguous"] = {
            "symbol": "BTC_USDT", "state": "SUBMITTED",
            "live_order_submitted": True,
        }
        restored = TradeCoordinator.restore_state(state)
        self.assertTrue(restored.halted)
        with self.assertRaisesRegex(RuntimeError, "halted"):
            restored.prepare_entry(
                idempotency_key="new", symbol="BTC_USDT", side="BUY",
                quantity="0.001", reference_price="50000",
                symbol_info=self.info, open_positions=[],
                stop_loss="49000", take_profit="53000",
                account_snapshot_age_seconds=1)

    def _verified_empty_account(self, observed_at=100):
        return {
            "verified": True,
            "source": "authenticated_futures_account",
            "observed_at": observed_at,
            "positions": [],
        }

    def test_reconcile_verified_empty_exchange_state_clears_local_prepared_intents(self):
        self.prepare()
        result = self.coordinator.reconcile_exchange_state(
            account_snapshot=self._verified_empty_account(),
            open_orders=[], now=101)
        self.assertTrue(result["reconciled"])
        self.assertFalse(result["halted"])
        self.assertEqual(result["cleared_local_intents"], 1)
        self.assertEqual(self.coordinator.export_state()["intents"], {})

    def test_reconcile_halts_when_exchange_reports_open_position(self):
        result = self.coordinator.reconcile_exchange_state(
            account_snapshot={
                **self._verified_empty_account(),
                "positions": [{"symbol": "BTCUSDT", "amount": 0.01}],
            }, open_orders=[], now=101)
        self.assertFalse(result["reconciled"])
        self.assertTrue(self.coordinator.halted)

    def test_reconcile_halts_when_exchange_reports_open_order(self):
        result = self.coordinator.reconcile_exchange_state(
            account_snapshot=self._verified_empty_account(),
            open_orders=[{"symbol": "BTC_USDT", "orderId": 7}], now=101)
        self.assertFalse(result["reconciled"])
        self.assertTrue(self.coordinator.halted)

    def test_reconcile_halts_on_unverified_or_stale_account(self):
        unverified = self._verified_empty_account()
        unverified["verified"] = False
        for snapshot, now in ((unverified, 101),
                              (self._verified_empty_account(1), 101)):
            coordinator = TradeCoordinator()
            result = coordinator.reconcile_exchange_state(
                account_snapshot=snapshot, open_orders=[], now=now)
            self.assertFalse(result["reconciled"])
            self.assertTrue(coordinator.halted)

    def test_reconcile_halts_on_malformed_open_orders(self):
        result = self.coordinator.reconcile_exchange_state(
            account_snapshot=self._verified_empty_account(),
            open_orders={"unexpected": "shape"}, now=101)
        self.assertFalse(result["reconciled"])
        self.assertTrue(self.coordinator.halted)

    def test_release_only_removes_local_prepared_intent(self):
        self.prepare()
        self.assertTrue(self.coordinator.release_prepared_intent("intent-1"))
        self.assertFalse(self.coordinator.release_prepared_intent("intent-1"))
        self.prepare(idempotency_key="intent-2")


    def test_emergency_stop_persists_across_restart(self):
        self.coordinator.emergency_stop("operator stop")
        restored = TradeCoordinator.restore_state(self.coordinator.export_state())
        self.assertTrue(restored.halted)
        self.assertEqual(restored.export_state()["halt_reason"], "operator stop")

    def test_corrupt_persisted_state_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported coordinator state"):
            TradeCoordinator.restore_state({"schema_version": 999})

    def test_restart_rejects_conflicting_pending_symbols(self):
        state = {
            "schema_version": 1,
            "halted": False,
            "intents": {
                "one": {"symbol": "BTC_USDT", "state": "PREPARED_ONLY",
                        "live_order_submitted": False},
                "two": {"symbol": "BTC_USDT", "state": "PREPARED_ONLY",
                        "live_order_submitted": False},
            },
        }
        restored = TradeCoordinator.restore_state(state)
        self.assertTrue(restored.halted)
        with self.assertRaisesRegex(RuntimeError, "halted"):
            self.prepare()

    def test_exported_state_contains_no_credentials(self):
        self.prepare()
        serialized = str(self.coordinator.export_state())
        self.assertNotIn("api_secret", serialized.lower())
        self.assertNotIn("api_key", serialized.lower())
        self.assertNotIn("secret", serialized.lower())


if __name__ == "__main__":
    unittest.main()
