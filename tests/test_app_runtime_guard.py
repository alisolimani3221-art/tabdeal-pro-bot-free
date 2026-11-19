import os
import tempfile
import unittest

from app_runtime_guard import AppRuntimeGuard


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


if __name__ == "__main__":
    unittest.main()
