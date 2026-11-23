import ast
import os
import tempfile
import unittest
from unittest.mock import patch

from trade_coordinator_session import TradeCoordinatorSession
from trade_state_lock import StateLockError
from trade_state_store import StateStoreError, TradeStateStore


class TradeCoordinatorSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "private", "trade-state.json")
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

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, session, **overrides):
        args = {
            "idempotency_key": "prepared-intent-1",
            "symbol": "BTC_USDT", "side": "BUY",
            "quantity": "0.001", "reference_price": "50000",
            "symbol_info": self.info, "open_positions": [],
            "stop_loss": "49000", "take_profit": "53000",
            "account_snapshot_age_seconds": 1,
        }
        args.update(overrides)
        return session.prepare_entry(**args)

    def test_fresh_session_persists_empty_state_and_prepared_intent(self):
        session = TradeCoordinatorSession(self.path, max_notional_usdt="100")
        self.assertEqual(session.snapshot()["intents"], {})
        intent = self.prepare(session)
        self.assertEqual(intent["state"], "PREPARED_ONLY")
        self.assertFalse(intent["live_order_submitted"])
        self.assertFalse(intent["execution_enabled"])
        self.assertEqual(
            TradeStateStore(self.path).load()["intents"]["prepared-intent-1"],
            intent,
        )

    def test_restart_recovers_intent_and_rejects_duplicate_key(self):
        first = TradeCoordinatorSession(self.path, max_notional_usdt="100")
        self.prepare(first)
        restarted = TradeCoordinatorSession(self.path, max_notional_usdt="100")
        self.assertIn("prepared-intent-1", restarted.snapshot()["intents"])
        with self.assertRaisesRegex(RuntimeError, "duplicate idempotency"):
            self.prepare(restarted)

    def test_corrupt_existing_state_blocks_session_start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{corrupt")
        with self.assertRaises(StateStoreError):
            TradeCoordinatorSession(self.path)

    def test_lock_contention_blocks_second_session(self):
        first_store = TradeStateStore(self.path)
        first = TradeCoordinatorSession(self.path)
        with first_store.exclusive():
            with self.assertRaises(StateLockError):
                TradeCoordinatorSession(self.path)
        self.assertFalse(first.snapshot()["halted"])

    def test_emergency_stop_is_durable_and_blocks_new_intents(self):
        session = TradeCoordinatorSession(self.path)
        result = session.emergency_stop("integration test stop")
        self.assertTrue(result["halted"])
        restarted = TradeCoordinatorSession(self.path)
        recovered = restarted.snapshot()
        self.assertTrue(recovered["halted"])
        self.assertEqual(recovered["halt_reason"], "integration test stop")
        self.assertEqual(recovered["intents"], {})
        with self.assertRaisesRegex(RuntimeError, "halted"):
            self.prepare(restarted)

    def test_persistence_failure_latches_session_closed(self):
        session = TradeCoordinatorSession(self.path)
        with patch.object(session.store, "save", side_effect=StateStoreError("disk failure")):
            with self.assertRaises(StateStoreError):
                self.prepare(session)
        with self.assertRaisesRegex(RuntimeError, "fail-closed"):
            session.snapshot()

    def test_session_module_has_no_network_or_live_adapter_imports(self):
        source_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "trade_coordinator_session.py",
        )
        with open(source_path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse({"requests", "websocket", "tabdeal_trade"} & imported)


if __name__ == "__main__":
    unittest.main()
