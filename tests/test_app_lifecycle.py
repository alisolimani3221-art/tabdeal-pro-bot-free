import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app_runtime_guard import AppRuntimeGuard
from paper_runner_gate import ensure_paper_runner_ready
from paper_trader import PaperTradingEngine
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
    "on_start", "on_stop", "_stop_auto_worker", "stop_auto_paper_trader", "start_auto_paper_trader", "_update_engine_health", "_schedule_rearmed_paper_runner", "request_rearm_paper_runner",
    "_persist_paper_engine_state"
}
METHOD_NODES = [
    node for node in APP_CLASS.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in METHOD_NAMES
]
LIFECYCLE_GLOBALS = {
    "os": os,
    "json": json,
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
    stop_auto_paper_trader = LIFECYCLE_GLOBALS["stop_auto_paper_trader"]
    start_auto_paper_trader = LIFECYCLE_GLOBALS["start_auto_paper_trader"]
    _update_engine_health = LIFECYCLE_GLOBALS["_update_engine_health"]
    _schedule_rearmed_paper_runner = LIFECYCLE_GLOBALS["_schedule_rearmed_paper_runner"]
    request_rearm_paper_runner = LIFECYCLE_GLOBALS["request_rearm_paper_runner"]
    _persist_paper_engine_state = LIFECYCLE_GLOBALS["_persist_paper_engine_state"]

    def __init__(self, user_data_dir):
        self.user_data_dir = user_data_dir
        self.auto_stop_event = threading.Event()
        self.auto_trader_thread = None
        self.auto_ws = None
        self.auto_engine = None
        self._paper_state_lock = threading.RLock()
        self.update_auto_status = Mock()


class FakeButton:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.handlers = {}

    def bind(self, **kwargs):
        self.handlers.update(kwargs)

    def press(self):
        self.handlers["on_press"](self)


class FakePopup:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.dismissed = False
        self.opened = False

    def dismiss(self):
        self.dismissed = True

    def open(self):
        self.opened = True


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
        # Keep the validated session available for guarded diagnostics/re-arm UI,
        # but the persisted halt must still block the paper runner.
        self.assertIsNotNone(second.coordinator_session)
        self.assertTrue(second.coordinator_session.snapshot()["halted"])
        self.assertIn("PaperRunnerGateError", second.coordinator_session_error)
        with self.assertRaisesRegex(Exception, "persistent emergency stop"):
            ensure_paper_runner_ready(second.runtime_guard, second.coordinator_session)
        second.on_stop()
        self.assertFalse(second.runtime_guard.held)

    def test_on_start_schedules_automatic_paper_runner_after_safety_initialization(self):
        clock = Mock()
        with patch.dict(LIFECYCLE_GLOBALS, {"Clock": clock}):
            app = LifecycleHarness(self.temp.name)
            app.start_auto_paper_trader = Mock()
            app.on_start()

        clock.schedule_once.assert_called_once()
        callback, delay = clock.schedule_once.call_args.args
        self.assertEqual(delay, 0.5)
        app.start_auto_paper_trader.assert_not_called()
        callback(0)
        app.start_auto_paper_trader.assert_called_once_with()
        app.on_stop()

    def test_confirmed_rearm_schedules_paper_runner_automatically(self):
        clock = Mock()
        with patch.dict(LIFECYCLE_GLOBALS, {"Clock": clock}):
            app = LifecycleHarness(self.temp.name)
            app.start_auto_paper_trader = Mock()
            app._schedule_rearmed_paper_runner()

        clock.schedule_once.assert_called_once()
        callback, delay = clock.schedule_once.call_args.args
        self.assertEqual(delay, 0.5)
        app.start_auto_paper_trader.assert_not_called()
        callback(0)
        app.start_auto_paper_trader.assert_called_once_with()

    def test_confirmed_rearm_callback_rearms_and_schedules_runner(self):
        buttons = []
        session = Mock()
        session.snapshot.return_value = {
            "halted": True,
            "halt_reason": "operator emergency stop",
            "intents": {},
        }
        guard = Mock(held=True)
        app = LifecycleHarness(self.temp.name)
        app.runtime_guard = guard
        app.coordinator_session = session
        app.auto_trader_thread = None
        app._schedule_rearmed_paper_runner = Mock()

        def make_button(**kwargs):
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        readiness = Mock()
        globals_patch = {
            "BoxLayout": Mock(return_value=Mock()),
            "Label": Mock(),
            "Button": make_button,
            "Popup": FakePopup,
            "dp": lambda value: value,
            "FONT_NAME": "test-font",
            "assistant_rtl_text": lambda value: value,
            "fa": lambda value: value,
            "fa_plain": lambda value: value,
            "ensure_paper_runner_ready": readiness,
        }
        with patch.dict(LIFECYCLE_GLOBALS, globals_patch):
            app.request_rearm_paper_runner()
            self.assertEqual(len(buttons), 2)
            # The callback resolves module globals when pressed, so keep the
            # injected readiness/UI dependencies patched for the full action.
            buttons[0].press()

        session.rearm_after_operator_stop.assert_called_once_with()
        readiness.assert_called_once_with(guard, session)
        app._schedule_rearmed_paper_runner.assert_called_once_with()

    def test_failed_rearm_readiness_rolls_back_halt_and_never_starts(self):
        buttons = []
        session = Mock()
        session.snapshot.return_value = {
            "halted": True,
            "halt_reason": "operator emergency stop",
            "intents": {},
        }
        guard = Mock(held=True)
        app = LifecycleHarness(self.temp.name)
        app.runtime_guard = guard
        app.coordinator_session = session
        app.auto_trader_thread = None
        app._schedule_rearmed_paper_runner = Mock()

        def make_button(**kwargs):
            button = FakeButton(**kwargs)
            buttons.append(button)
            return button

        readiness = Mock(side_effect=RuntimeError("readiness rejected"))
        globals_patch = {
            "BoxLayout": Mock(return_value=Mock()),
            "Label": Mock(),
            "Button": make_button,
            "Popup": FakePopup,
            "dp": lambda value: value,
            "FONT_NAME": "test-font",
            "assistant_rtl_text": lambda value: value,
            "fa": lambda value: value,
            "fa_plain": lambda value: value,
            "ensure_paper_runner_ready": readiness,
        }
        with patch.dict(LIFECYCLE_GLOBALS, globals_patch):
            app.request_rearm_paper_runner()
            buttons[0].press()

        session.rearm_after_operator_stop.assert_called_once_with()
        readiness.assert_called_once_with(guard, session)
        session.emergency_stop.assert_called_once_with("operator emergency stop")
        app._schedule_rearmed_paper_runner.assert_not_called()
        self.assertIn("بازآماده‌سازی انجام نشد", app.update_auto_status.call_args.args[0])

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

    def test_emergency_stop_closes_and_persists_even_when_worker_is_still_alive(self):
        app = LifecycleHarness(self.temp.name)
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([{
            "symbol": "AAA_USDT", "price": 100, "sma10": 99, "sma20": 98,
            "rsi14": 56, "atr14": 0.8, "signal": "BULLISH",
            "closed_candles": 23, "candle_open_time": 60_000,
            "timeframe_analysis": {"ready": True, "agreement": True,
            "direction": "BULLISH", "entry_trigger": True,
            "entry_trigger_direction": "BULLISH", "required_minutes": [1, 5, 15],
            "closed_candles": {"1": 1439, "5": 288, "15": 96},
            "signals": {
                "1": "BULLISH", "5": "BULLISH", "15": "BULLISH"},
            "risk_range_by_timeframe": {"5": 1.6}},
        }])
        self.assertIsNotNone(engine.position)

        class SlowWorker:
            def __init__(self):
                self.join_calls = []
            def is_alive(self):
                return True
            def join(self, timeout):
                self.join_calls.append(timeout)

        worker = SlowWorker()
        socket = Mock()
        app.auto_engine = engine
        app.auto_trader_thread = worker
        app.auto_ws = socket

        app.stop_auto_paper_trader()

        self.assertTrue(app.auto_stop_event.is_set())
        socket.close.assert_called_once_with()
        self.assertEqual(worker.join_calls, [0.0, 3.0])
        self.assertTrue(engine.halted)
        self.assertEqual(engine.halt_reason, "EMERGENCY_STOP")
        self.assertIsNone(engine.position)
        self.assertIn("موقعیت مجازی", app.update_auto_status.call_args.args[0])
        state_path = os.path.join(self.temp.name, "paper-engine-state.json")
        with open(state_path, "r", encoding="utf-8") as state_file:
            recovered = PaperTradingEngine.restore_state(json.load(state_file))
        self.assertTrue(recovered.halted)
        self.assertIsNone(recovered.position)
        blocked = recovered.process_scan([{
            "symbol": "AAA_USDT", "price": 101, "sma10": 99, "sma20": 98,
            "rsi14": 56, "atr14": 0.8, "signal": "BULLISH",
            "closed_candles": 21, "candle_open_time": 120_000,
        }])
        self.assertTrue(blocked["halted"])
        self.assertEqual(blocked["halt_reason"], "EMERGENCY_STOP")
        self.assertEqual(blocked["events"], [])
        self.assertIsNone(blocked["open_position"])

    def test_worker_treats_restored_open_position_as_a_recovery_data_gap(self):
        source = MAIN_SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            'recovered_open_symbol = (engine.position.get("symbol")', source
        )
        self.assertIn("nonlocal recovered_open_symbol", source)
        self.assertIn("recovered_gap = recovered_open_symbol == symbol", source)
        self.assertIn("recovered_gap = recovered_open_symbol == event_symbol", source)
        self.assertIn("coordinator.reconcile_tick(", source)
        self.assertIn("recovered_open_symbol = None", source)
    def test_open_position_market_silence_is_checked_independently_of_socket_liveness(self):
        source = MAIN_SOURCE.read_text(encoding="utf-8")
        self.assertIn('market_connection_state = {"opened_at": None}', source)
        self.assertIn('position_market = market_status.get(position_symbol, {})', source)
        self.assertIn("position_age > 30.0", source)
        self.assertIn("engine.halt_for_data_gap(", source)
        self.assertIn('current_position.get("stop")', source)
        self.assertIn("position market stale; protective gap close failed", source)
        self.assertIn("شبیه‌ساز متوقف شد", source)


    def test_websocket_close_callback_records_close_reason_for_diagnostics(self):
        source = MAIN_SOURCE.read_text(encoding="utf-8")
        start = source.index("            def on_close(ws, close_status_code, close_message):")
        end = source.index("\n            scan_stop = threading.Event()", start)
        close_callback = source[start:end]
        self.assertIn("close_status_code", close_callback)
        self.assertIn("last_stream_error=detail", close_callback)
        self.assertIn("websocket_closed:", close_callback)

    def test_repeated_start_does_not_reset_health_of_running_worker(self):
        app = LifecycleHarness(self.temp.name)
        worker = Mock()
        worker.is_alive.return_value = True
        app.auto_trader_thread = worker
        app.auto_health = {"health_state": "ACTIVE", "selected_symbol_trade_count": 7}

        app.start_auto_paper_trader()

        self.assertEqual(app.auto_health["health_state"], "ACTIVE")
        self.assertEqual(app.auto_health["selected_symbol_trade_count"], 7)
        app.update_auto_status.assert_called_once()

    def test_start_gate_blocks_worker_when_runtime_lock_is_not_held(self):
        app = LifecycleHarness(self.temp.name)
        app.runtime_guard = Mock(held=False)
        app.coordinator_session = Mock()

        app.start_auto_paper_trader()

        app.update_auto_status.assert_called_once()
        self.assertIn("شروع شبیه‌ساز مسدود شد", app.update_auto_status.call_args.args[0])
        self.assertIsNone(app.auto_trader_thread)

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


    def test_concurrent_paper_state_persistence_is_serialized_and_atomic(self):
        app = LifecycleHarness(self.temp.name)
        engine = PaperTradingEngine(capital=8)
        errors = []

        def persist_many():
            try:
                for _ in range(25):
                    app._persist_paper_engine_state(engine)
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=persist_many) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        state_path = os.path.join(self.temp.name, "paper-engine-state.json")
        with open(state_path, "r", encoding="utf-8") as state_file:
            restored = PaperTradingEngine.restore_state(json.load(state_file))
        self.assertEqual(restored.cash, engine.cash)
        self.assertFalse(os.path.exists(state_path + ".tmp"))



    def test_shutdown_persists_open_paper_position_for_tick_recovery(self):
        app = LifecycleHarness(self.temp.name)
        app.on_start()
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([{
            "symbol": "AAA_USDT", "price": 100, "sma10": 99, "sma20": 98,
            "rsi14": 56, "atr14": 0.8, "signal": "BULLISH",
            "closed_candles": 23, "candle_open_time": 60_000,
            "timeframe_analysis": {"ready": True, "agreement": True,
            "direction": "BULLISH", "entry_trigger": True,
            "entry_trigger_direction": "BULLISH", "required_minutes": [1, 5, 15],
            "closed_candles": {"1": 1439, "5": 288, "15": 96},
            "signals": {
                "1": "BULLISH", "5": "BULLISH", "15": "BULLISH"},
            "risk_range_by_timeframe": {"5": 1.6}},
        }])
        self.assertIsNotNone(engine.position)
        app.auto_engine = engine

        app.on_stop()

        state_path = os.path.join(self.temp.name, "paper-engine-state.json")
        with open(state_path, "r", encoding="utf-8") as state_file:
            recovered = PaperTradingEngine.restore_state(json.load(state_file))
        self.assertEqual(recovered.position["symbol"], "AAA_USDT")
        self.assertFalse(recovered.halted)
        self.assertFalse(os.path.exists(state_path + ".tmp"))

    def test_ui_emergency_stop_persists_coordinator_halt_across_app_restart(self):
        first = LifecycleHarness(self.temp.name)
        first.on_start()
        self.assertIsNotNone(first.coordinator_session)
        self.assertFalse(first.coordinator_session.snapshot()["halted"])

        first.stop_auto_paper_trader()
        self.assertTrue(first.coordinator_session.snapshot()["halted"])
        first.on_stop()
        self.assertFalse(first.runtime_guard.held)

        restarted = LifecycleHarness(self.temp.name)
        restarted.on_start()
        self.assertIsNotNone(restarted.coordinator_session)
        self.assertTrue(restarted.coordinator_session.snapshot()["halted"])
        self.assertIn("PaperRunnerGateError", restarted.coordinator_session_error)
        with self.assertRaisesRegex(Exception, "persistent emergency stop"):
            ensure_paper_runner_ready(restarted.runtime_guard, restarted.coordinator_session)
        restarted.on_stop()
        self.assertFalse(restarted.runtime_guard.held)


    def test_shutdown_persistence_failure_latches_durable_coordinator_halt(self):
        app = LifecycleHarness(self.temp.name)
        app.on_start()
        self.assertIsNotNone(app.coordinator_session)
        app.auto_engine = Mock()
        app._persist_paper_engine_state = Mock(
            side_effect=OSError("simulated disk write failure")
        )

        app.on_stop()

        state = app.coordinator_session.snapshot()
        self.assertTrue(state["halted"])
        self.assertIn("persistence", state["halt_reason"].lower())
        self.assertTrue(any(
            "ذخیره" in str(call.args[0]) or "persist" in str(call.args[0]).lower()
            for call in app.update_auto_status.call_args_list
        ))
        self.assertFalse(app.runtime_guard.held)


if __name__ == "__main__":
    unittest.main()
