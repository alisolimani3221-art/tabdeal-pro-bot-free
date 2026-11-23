import ast
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app_runtime_guard import AppRuntimeGuard
from paper_runner_gate import ensure_paper_runner_ready
from trade_coordinator_session import TradeCoordinatorSession


# Load the actual lifecycle method bodies from main.py without importing Kivy.
# The CI validation image intentionally does not install the Android UI runtime.
MAIN_SOURCE = Path(__file__).resolve().parents[1] / "main.py"
MAIN_TREE = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
APP_CLASS = next(
    node for node in MAIN_TREE.body
    if isinstance(node, ast.ClassDef) and node.name == "FuturesAnalyzerApp"
)
METHOD_NAMES = {
    "on_start", "on_stop", "_stop_auto_worker", "start_auto_paper_trader",
    "stop_auto_paper_trader"
}
METHOD_NODES = [
    node for node in APP_CLASS.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in METHOD_NAMES
]
LIFECYCLE_GLOBALS = {
    "os": os,
    "threading": threading,
    "AppRuntimeGuard": AppRuntimeGuard,
    "TradeCoordinatorSession": TradeCoordinatorSession,
    "ensure_paper_runner_ready": ensure_paper_runner_ready,
    "Clock": Mock(),
}
exec(
    compile(ast.Module(body=METHOD_NODES, type_ignores=[]), str(MAIN_SOURCE), "exec"),
    LIFECYCLE_GLOBALS,
)


class LifecycleHarness:
    """Exercise the real lifecycle method bodies without creating a Kivy window."""

    on_start = LIFECYCLE_GLOBALS["on_start"]
    on_stop = LIFECYCLE_GLOBALS["on_stop"]
    _stop_auto_worker = LIFECYCLE_GLOBALS["_stop_auto_worker"]
    start_auto_paper_trader = LIFECYCLE_GLOBALS["start_auto_paper_trader"]
    stop_auto_paper_trader = LIFECYCLE_GLOBALS["stop_auto_paper_trader"]

    def __init__(self, user_data_dir):
        self.user_data_dir = user_data_dir
        self.auto_stop_event = threading.Event()
        self.auto_trader_thread = None
        self.auto_ws = None
        self.update_auto_status = Mock()
        self.market_refresh_stop_event = threading.Event()
        self.market_refresh_thread = None
        self.show_status = Mock()

    def _market_refresh_loop(self):
        return None


class AppLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_start_stop_restart_recovers_persisted_emergency_halt(self):
        first = LifecycleHarness(self.temp.name)
        first.on_start()

        self.assertTrue(first.runtime_lock_ready)
        self.assertTrue(first.runtime_guard.held)
        self.assertIsNotNone(first.coordinator_session)
        first.coordinator_session.emergency_stop("lifecycle integration test")
        first.on_stop()
        self.assertFalse(first.runtime_guard.held)

        # A fresh app instance must reload the durable halt and fail closed.
        second = LifecycleHarness(self.temp.name)
        second.on_start()
        self.assertTrue(second.runtime_lock_ready)
        self.assertTrue(second.runtime_guard.held)
        self.assertIsNone(second.coordinator_session)
        self.assertIn("PaperRunnerGateError", second.coordinator_session_error)
        second.on_stop()
        self.assertFalse(second.runtime_guard.held)

    def test_runtime_lock_failure_prevents_session_initialization(self):
        guard = Mock()
        guard.start.return_value = False
        guard.error = "simulated flock failure"

        with patch.dict(LIFECYCLE_GLOBALS, {"AppRuntimeGuard": Mock(return_value=guard)}) as patched:
            session_factory = Mock()
            patched["TradeCoordinatorSession"] = session_factory
            app = LifecycleHarness(self.temp.name)
            app.on_start()

        self.assertFalse(app.runtime_lock_ready)
        session_factory.assert_not_called()
        self.assertIsNone(app.coordinator_session)
        self.assertEqual(app.runtime_lock_error, "simulated flock failure")

    def test_on_stop_keeps_runtime_lock_when_worker_does_not_exit(self):
        app = LifecycleHarness(self.temp.name)
        app.on_start()
        self.assertTrue(app.runtime_guard.held)

        class StuckWorker:
            alive = True

            def is_alive(self):
                return self.alive

            def join(self, timeout):
                self.join_timeout = timeout

        worker = StuckWorker()
        socket = Mock()
        app.auto_trader_thread = worker
        app.auto_ws = socket

        app.on_stop()
        socket.close.assert_called_once_with()
        self.assertEqual(worker.join_timeout, 3.0)
        self.assertTrue(app.runtime_guard.held)

        # Once the worker is confirmed stopped, a later shutdown callback may
        # safely release the app-wide lock.
        worker.alive = False
        app.on_stop()
        self.assertFalse(app.runtime_guard.held)

    def test_start_gate_blocks_worker_when_runtime_lock_is_not_held(self):
        app = LifecycleHarness(self.temp.name)
        app.runtime_guard = Mock(held=False)
        app.coordinator_session = Mock()

        app.start_auto_paper_trader()

        app.update_auto_status.assert_called_once()
        self.assertIn("شروع شبیه‌ساز مسدود شد", app.update_auto_status.call_args.args[0])
        self.assertIsNone(app.auto_trader_thread)


    def test_emergency_stop_closes_engine_and_persists_durable_halt(self):
        app = LifecycleHarness(self.temp.name)
        app._stop_auto_worker = Mock(return_value=True)
        app.coordinator_session = Mock()
        app.auto_engine = Mock()
        app.auto_engine.emergency_close.return_value = {
            "event": "STOPPED", "trade": None
        }

        app.stop_auto_paper_trader()

        app._stop_auto_worker.assert_called_once_with(timeout=3.0)
        app.coordinator_session.emergency_stop.assert_called_once_with(
            "operator emergency stop"
        )
        app.auto_engine.emergency_close.assert_called_once_with()
        self.assertTrue(app._paper_emergency_latched)
        self.assertIn("توقف اضطراری فعال شد", app.update_auto_status.call_args.args[0])

    def test_emergency_stop_does_not_claim_success_when_worker_is_stuck(self):
        app = LifecycleHarness(self.temp.name)
        app._stop_auto_worker = Mock(return_value=False)
        app.coordinator_session = Mock()
        app.auto_engine = Mock()

        app.stop_auto_paper_trader()

        app.coordinator_session.emergency_stop.assert_not_called()
        app.auto_engine.emergency_close.assert_not_called()
        self.assertIn("پایان کامل رشته", app.update_auto_status.call_args.args[0])

    def test_persisted_state_is_readable_after_clean_restart(self):
        app = LifecycleHarness(self.temp.name)
        app.on_start()
        self.assertIsNotNone(app.coordinator_session)
        app.on_stop()

        recovered = TradeCoordinatorSession(
            os.path.join(self.temp.name, "trade-state.json"),
            max_open_positions=1,
        )
        snapshot = recovered.snapshot()
        self.assertIs(snapshot["halted"], False)
        self.assertEqual(snapshot["intents"], {})


if __name__ == "__main__":
    unittest.main()
