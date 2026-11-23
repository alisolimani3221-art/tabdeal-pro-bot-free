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

    def test_constructor_rejects_invalid_notional_caps(self):
        for value in (True, False, 0, -1, "NaN", "Infinity", "-Infinity", "bad"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_notional_usdt"):
                TradeCoordinator(max_notional_usdt=value)

    def test_constructor_normalizes_decimal_notional_cap(self):
        coordinator = TradeCoordinator(max_notional_usdt="100.000")
        self.assertEqual(coordinator.max_notional_usdt, "100.000")

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

    def test_pending_intent_consumes_global_position_capacity(self):
        self.prepare()
        eth_info = copy.deepcopy(self.info)
        eth_info["symbol"] = "ETH_USDT"
        with self.assertRaisesRegex(RuntimeError, "including pending intents"):
            self.prepare(
                idempotency_key="intent-2", symbol="ETH_USDT",
                symbol_info=eth_info,
            )

    def test_open_positions_and_pending_intents_share_global_capacity(self):
        coordinator = TradeCoordinator(max_open_positions=2, max_notional_usdt="100")
        coordinator.prepare_entry(
            idempotency_key="intent-1", symbol="BTC_USDT", side="BUY",
            quantity="0.001", reference_price="50000", symbol_info=self.info,
            open_positions=[], stop_loss="49000", take_profit="53000",
            account_snapshot_age_seconds=1,
        )
        eth_info = copy.deepcopy(self.info)
        eth_info["symbol"] = "ETH_USDT"
        with self.assertRaisesRegex(RuntimeError, "including pending intents"):
            coordinator.prepare_entry(
                idempotency_key="intent-2", symbol="ETH_USDT", side="BUY",
                quantity="0.001", reference_price="50000", symbol_info=eth_info,
                open_positions=[{"symbol": "XRP_USDT", "positionAmt": "1"}],
                stop_loss="49000", take_profit="53000",
                account_snapshot_age_seconds=1,
            )

    def test_stale_account_snapshot_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "stale"):
            self.prepare(account_snapshot_age_seconds=11)

    def test_non_finite_account_snapshot_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "age is invalid"):
                self.prepare(account_snapshot_age_seconds=value)

    def test_non_finite_max_snapshot_age_rejected(self):
        with self.assertRaisesRegex(ValueError, "age is invalid"):
            self.prepare(max_snapshot_age_seconds="Infinity")

    def test_missing_protective_order_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "both protective"):
            self.prepare(take_profit=None)

    def test_non_finite_stop_or_target_rejected(self):
        for field in ("stop_loss", "take_profit"):
            for value in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "finite and positive"):
                    self.prepare(**{field: value})

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
    def test_later_stop_request_does_not_overwrite_original_halt_reason(self):
        first = self.coordinator.emergency_stop("restart found ambiguous execution state")
        second = self.coordinator.emergency_stop("operator emergency stop")
        self.assertEqual(first["reason"], "restart found ambiguous execution state")
        self.assertEqual(second["reason"], "restart found ambiguous execution state")
        with self.assertRaisesRegex(RuntimeError, "only an explicit operator"):
            self.coordinator.rearm_after_operator_stop()


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

    def test_restore_rejects_boolean_schema_version(self):
        with self.assertRaisesRegex(ValueError, "unsupported coordinator state"):
            TradeCoordinator.restore_state({"schema_version": True, "intents": {}})

    def test_restore_rejects_incomplete_top_level_state(self):
        for state in (
            {"schema_version": 1},
            {"schema_version": 1, "halted": False, "halt_reason": None},
            {"schema_version": 1, "halted": False, "halt_reason": None,
             "intents": {}, "unexpected": "field"},
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                TradeCoordinator.restore_state(state)

    def test_restore_rejects_invalid_halt_metadata(self):
        base = {
            "schema_version": 1, "halted": False,
            "halt_reason": None, "intents": {},
        }
        invalid_states = []
        for value in (0, 1, "false", None):
            state = copy.deepcopy(base)
            state["halted"] = value
            invalid_states.append(state)
        state = copy.deepcopy(base)
        state["halt_reason"] = "stale reason while not halted"
        invalid_states.append(state)
        state = copy.deepcopy(base)
        state["halted"] = True
        state["halt_reason"] = None
        invalid_states.append(state)
        for state in invalid_states:
            with self.subTest(state=state), self.assertRaises(ValueError):
                TradeCoordinator.restore_state(state)

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

    def test_restart_with_malformed_prepared_intent_halts(self):
        self.prepare()
        state = self.coordinator.export_state()
        state["intents"]["intent-1"]["execution_enabled"] = True
        restored = TradeCoordinator.restore_state(state)
        self.assertTrue(restored.halted)

    def test_restart_with_excess_pending_intents_halts(self):
        self.prepare()
        state = self.coordinator.export_state()
        second = copy.deepcopy(state["intents"]["intent-1"])
        second["idempotency_key"] = "intent-2"
        second["symbol"] = "ETH_USDT"
        state["intents"]["intent-2"] = second
        restored = TradeCoordinator.restore_state(state, max_open_positions=1)
        self.assertTrue(restored.halted)

    def test_restart_with_inconsistent_persisted_notional_halts(self):
        self.prepare()
        state = self.coordinator.export_state()
        state["intents"]["intent-1"]["notional_usdt"] = "999999"
        restored = TradeCoordinator.restore_state(state)
        self.assertTrue(restored.halted)

    def test_release_only_removes_local_prepared_intent(self):
        self.prepare()
        self.assertTrue(self.coordinator.release_prepared_intent("intent-1"))
        self.assertFalse(self.coordinator.release_prepared_intent("intent-1"))
        self.prepare(idempotency_key="intent-2")


if __name__ == "__main__":
    unittest.main()
