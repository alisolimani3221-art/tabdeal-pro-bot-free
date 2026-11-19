import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import trade_state_lock
from trade_state_lock import StateLockError, TradeStateLock


class TradeStateLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.temp.name, "trade-state.json")

    def tearDown(self):
        self.temp.cleanup()

    def _child(self, code):
        env = dict(os.environ)
        root = os.path.dirname(os.path.dirname(__file__))
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-c", code, self.state_path],
            env=env, text=True, capture_output=True, timeout=10
        )

    def test_second_process_cannot_acquire_while_parent_holds_lock(self):
        lock = TradeStateLock(self.state_path)
        with lock:
            result = self._child(
                "import sys; from trade_state_lock import TradeStateLock, StateLockError; "
                "p=sys.argv[1]; "
                "exec('try:\\n TradeStateLock(p).acquire()\\nexcept StateLockError:\\n sys.exit(23)'); "
                "sys.exit(0)"
            )
            self.assertEqual(result.returncode, 23, result.stderr)
        self.assertTrue(os.path.exists(self.state_path + ".lock"))

    def test_lock_is_available_after_release(self):
        with TradeStateLock(self.state_path):
            pass
        with TradeStateLock(self.state_path):
            self.assertTrue(True)

    def test_kernel_releases_lock_after_abnormal_process_exit(self):
        result = self._child(
            "import os,sys; from trade_state_lock import TradeStateLock; "
            "lock=TradeStateLock(sys.argv[1]); lock.acquire(); os._exit(0)"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with TradeStateLock(self.state_path):
            self.assertTrue(True)

    def test_symlink_lock_path_is_rejected(self):
        target = os.path.join(self.temp.name, "target")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("x")
        try:
            os.symlink(target, self.state_path + ".lock")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(StateLockError):
            TradeStateLock(self.state_path).acquire()

    def test_non_regular_lock_path_is_rejected(self):
        os.mkdir(self.state_path + ".lock")
        with self.assertRaises(StateLockError):
            TradeStateLock(self.state_path).acquire()

    def test_missing_flock_support_fails_closed(self):
        with mock.patch.object(trade_state_lock, "fcntl", None):
            with self.assertRaises(StateLockError):
                TradeStateLock(self.state_path)

    def test_nested_acquire_is_reentrant_for_same_thread(self):
        lock = TradeStateLock(self.state_path)
        with lock:
            with lock:
                self.assertTrue(lock.held)
            self.assertTrue(lock.held)
        self.assertFalse(lock.held)

    def test_release_does_not_unlink_lock_file(self):
        lock = TradeStateLock(self.state_path)
        with lock:
            lock_file = lock.lock_path
        self.assertTrue(os.path.isfile(lock_file))


if __name__ == "__main__":
    unittest.main()
