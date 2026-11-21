import unittest

from paper_scan_gate import PaperScanGate


class PaperScanGateTests(unittest.TestCase):
    def test_first_fresh_snapshot_is_scanned_immediately(self):
        gate = PaperScanGate()
        self.assertEqual(gate.evaluate(100.0, 100.0), (True, "ready", 0.0))

    def test_successful_scans_are_at_least_fifteen_seconds_apart(self):
        gate = PaperScanGate()
        allowed, _, _ = gate.evaluate(100.0, 100.0)
        self.assertTrue(allowed)
        gate.mark_scanned(100.0)
        self.assertEqual(gate.evaluate(114.9, 114.9)[1], "interval_not_elapsed")
        self.assertTrue(gate.evaluate(115.0, 115.0)[0])

    def test_stale_snapshot_blocks_scan_and_fresh_recovery_resumes_immediately(self):
        gate = PaperScanGate()
        gate.mark_scanned(100.0)
        allowed, reason, age = gate.evaluate(140.0, 109.0)
        self.assertFalse(allowed)
        self.assertEqual(reason, "stale_snapshot")
        self.assertEqual(age, 31.0)
        recovered = gate.evaluate(141.0, 141.0)
        self.assertEqual(recovered, (True, "ready", 0.0))

    def test_missing_or_invalid_snapshot_never_scans(self):
        gate = PaperScanGate()
        self.assertEqual(gate.evaluate(100.0, None)[1], "waiting_for_snapshot")
        self.assertEqual(gate.evaluate(100.0, float("nan"))[1], "invalid_timestamp")
        self.assertFalse(gate.evaluate(100.0, 100.0, snapshot_available=False)[0])

    def test_invalid_configuration_is_rejected(self):
        for value in (True, 0, -1, float("inf"), float("nan"), "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PaperScanGate(interval_seconds=value)


if __name__ == "__main__":
    unittest.main()
