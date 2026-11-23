import unittest
from unittest.mock import Mock

from paper_runner_gate import PaperRunnerGateError, ensure_paper_runner_ready


class PaperRunnerGateTests(unittest.TestCase):
    def setUp(self):
        self.guard = Mock(held=True)
        self.session = Mock()
        self.session.snapshot.return_value = {"halted": False, "intents": {}}

    def test_all_safety_conditions_allow_paper_runner(self):
        self.assertTrue(ensure_paper_runner_ready(self.guard, self.session))

    def test_missing_or_unheld_runtime_lock_blocks(self):
        with self.assertRaises(PaperRunnerGateError):
            ensure_paper_runner_ready(None, self.session)
        self.guard.held = False
        with self.assertRaises(PaperRunnerGateError):
            ensure_paper_runner_ready(self.guard, self.session)

    def test_missing_persistent_session_blocks(self):
        with self.assertRaises(PaperRunnerGateError):
            ensure_paper_runner_ready(self.guard, None)

    def test_persisted_emergency_stop_blocks(self):
        self.session.snapshot.return_value = {"halted": True, "intents": {}}
        with self.assertRaisesRegex(PaperRunnerGateError, "emergency stop"):
            ensure_paper_runner_ready(self.guard, self.session)

    def test_persisted_emergency_stop_reports_the_saved_reason(self):
        self.session.snapshot.return_value = {
            "halted": True,
            "halt_reason": "worker-side emergency stop",
            "intents": {},
        }
        with self.assertRaisesRegex(
                PaperRunnerGateError, "worker-side emergency stop"):
            ensure_paper_runner_ready(self.guard, self.session)

    def test_malformed_or_unreadable_state_blocks(self):
        for state in (None, [], {"halted": "false", "intents": {}},
                      {"halted": False, "intents": []}):
            self.session.snapshot.return_value = state
            with self.assertRaises(PaperRunnerGateError):
                ensure_paper_runner_ready(self.guard, self.session)
        self.session.snapshot.side_effect = OSError("state read failed")
        with self.assertRaises(OSError):
            ensure_paper_runner_ready(self.guard, self.session)


if __name__ == "__main__":
    unittest.main()
