import json
import os
import tempfile
import unittest
from unittest import mock

from paper_engine_state_store import PaperStateStoreError, atomic_write_json


class PaperEngineStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "nested", "paper-engine-state.json")
        self.state = {"schema_version": 4, "cash": 8.0, "halted": False}

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_snapshot_round_trip_is_private_and_leaves_no_temp_file(self):
        atomic_write_json(self.path, self.state)
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), self.state)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), [
            "paper-engine-state.json"
        ])

    def test_directory_fsync_failure_is_reported_after_replace(self):
        real_fsync = os.fsync
        calls = []

        def fail_second_fsync(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError("simulated directory fsync failure")
            return real_fsync(fd)

        with mock.patch("paper_engine_state_store.os.fsync",
                        side_effect=fail_second_fsync):
            with self.assertRaises(PaperStateStoreError):
                atomic_write_json(self.path, self.state)

        # os.replace may have committed; the caller still receives a failure
        # and must stop rather than claim a durable commit.
        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), self.state)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), [
            "paper-engine-state.json"
        ])

    def test_replace_failure_preserves_previous_snapshot_and_cleans_temp(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        previous = {"schema_version": 4, "cash": 7.5, "halted": True}
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(previous, handle)

        with mock.patch("paper_engine_state_store.os.replace",
                        side_effect=OSError("simulated replace failure")):
            with self.assertRaises(PaperStateStoreError):
                atomic_write_json(self.path, self.state)

        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), previous)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), [
            "paper-engine-state.json"
        ])

    def test_non_finite_json_is_rejected_before_creating_temp_file(self):
        with self.assertRaises(PaperStateStoreError):
            atomic_write_json(self.path, {"schema_version": 4, "cash": float("nan")})
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))


if __name__ == "__main__":
    unittest.main()
