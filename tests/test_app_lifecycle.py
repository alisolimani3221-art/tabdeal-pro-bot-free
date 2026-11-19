import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import main
from trade_coordinator_session import TradeCoordinatorSession


class LifecycleHarness:
    """Exercise the real Kivy lifecycle methods without creating a UI window."""

    on_start = main.FuturesAnalyzerApp.on_start
    on_stop = main.FuturesAnalyzerApp.on_stop
    _stop_auto_worker = main.FuturesAnalyzerApp._stop_auto_worker

    def __init__(self, user_data_dir):
        self.user_data_dir = user_data_dir
        self.auto_stop_event = threading.Event()
        self.auto_trader_thread = None
        self.auto_ws = None
        self.update_auto_status = Mock()


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

        with patch("main.AppRuntimeGuard", return_value=guard), \
             patch("main.TradeCoordinatorSession") as session_factory:
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
        app.update_auto_status = Mock()

        main.FuturesAnalyzerApp.start_auto_paper_trader(app)

        app.update_auto_status.assert_called_once()
        self.assertIn("شروع شبیه‌ساز مسدود شد", app.update_auto_status.call_args.args[0])
        self.assertFalse(hasattr(app, "auto_trader_thread"))

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
