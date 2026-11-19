import json
import os
import tempfile
import unittest

from trade_state_store import StateStoreError, TradeStateStore


class TradeStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "state", "trade-state.json")
        self.store = TradeStateStore(self.path)
        self.state = {
            "schema_version": 1,
            "halted": False,
            "halt_reason": None,
            "intents": {},
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_state_is_the_only_empty_start_case(self):
        self.assertIsNone(self.store.load())

    def test_round_trip_and_private_file_permissions(self):
        self.store.save(self.state)
        self.assertEqual(self.store.load(), self.state)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_corrupt_json_fails_closed(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        with self.assertRaises(StateStoreError):
            self.store.load()

    def test_duplicate_keys_and_non_finite_values_are_rejected(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for payload in (
            '{"schema_version":1,"schema_version":1}',
            '{"schema_version":1,"value":NaN}',
        ):
            with self.subTest(payload=payload):
                with open(self.path, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                with self.assertRaises(StateStoreError):
                    self.store.load()

    def test_unknown_schema_is_rejected(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 99}, handle)
        with self.assertRaises(StateStoreError):
            self.store.load()

    def test_save_rejects_non_json_and_oversized_state(self):
        with self.assertRaises(StateStoreError):
            self.store.save({"schema_version": 1, "value": float("nan")})
        with self.assertRaises(StateStoreError):
            self.store.save({"schema_version": 1, "data": "x" * (1024 * 1024)})

    def test_symlink_state_is_rejected(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        target = os.path.join(self.temp.name, "target.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle)
        try:
            os.symlink(target, self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(StateStoreError):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
