import os
import tempfile
import unittest
from unittest.mock import patch

from app_runtime_guard import AppRuntimeGuard
from trade_state_lock import StateLockError


class AppRuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_guard_uses_private_directory_and_releases_on_stop(self):
        guard = AppRuntimeGuard(self.temp.name)
        self.assertTrue(guard.start())
        self.assertTrue(guard.held)
        self.assertTrue(os.path.isfile(os.path.join(self.temp.name, "app-runtime.lock")))
        self.assertTrue(guard.stop())
        self.assertFalse(guard.held)
        self.assertTrue(guard.start())
        self.assertTrue(guard.stop())

    def test_second_app_instance_fails_closed(self):
        first = AppRuntimeGuard(self.temp.name)
        second = AppRuntimeGuard(self.temp.name)
        self.assertTrue(first.start())
        self.assertFalse(second.start())
        self.assertIsNotNone(second.error)
        self.assertTrue(first.held)
        self.assertTrue(first.stop())
        self.assertTrue(second.start())
        self.assertTrue(second.stop())

    def test_stop_is_idempotent_for_unstarted_guard(self):
        guard = AppRuntimeGuard(self.temp.name)
        self.assertFalse(guard.stop())
        self.assertFalse(guard.held)

    def test_unsupported_lock_disables_guard_without_crashing_app(self):
        guard = AppRuntimeGuard(self.temp.name)
        with patch("app_runtime_guard.TradeStateLock", side_effect=StateLockError("flock unavailable")):
            self.assertFalse(guard.start())
        self.assertFalse(guard.held)
        self.assertIn("flock unavailable", guard.error)
        # A transient unsupported condition can be retried safely.
        self.assertTrue(guard.start())
        self.assertTrue(guard.stop())


if __name__ == "__main__":
    unittest.main()
