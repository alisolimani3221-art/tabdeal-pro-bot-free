import unittest

from server_paper_trader import load_config


class MonitorConfigTests(unittest.TestCase):
    def test_defaults_are_safe_and_bounded(self):
        api, interval, workers = load_config({})
        self.assertEqual(api, "https://api1.tabdeal.org/r/fapi/v1")
        self.assertEqual(interval, 300)
        self.assertEqual(workers, 8)

    def test_scan_interval_and_worker_count_are_clamped(self):
        self.assertEqual(load_config({"PAPER_SCAN_SECONDS": "10"})[1], 60)
        self.assertEqual(load_config({"PAPER_WORKERS": "99"})[2], 12)
        self.assertEqual(load_config({"PAPER_WORKERS": "0"})[2], 1)

    def test_rejects_non_integer_environment_values(self):
        for name in ("PAPER_SCAN_SECONDS", "PAPER_WORKERS"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, name):
                    load_config({name: "not-a-number"})

    def test_requires_https_api_url(self):
        for api in ("http://api1.tabdeal.org/r/fapi/v1",
                    "ftp://api1.tabdeal.org/r/fapi/v1",
                    "https://user:pass@api1.tabdeal.org/r/fapi/v1",
                    "https://api1.tabdeal.org/r/fapi/v1?key=value",
                    "https://api1.tabdeal.org/r/fapi/v1#fragment",
                    "https://api1.tabdeal.org/a/../b"):
            with self.subTest(api=api):
                with self.assertRaises(ValueError):
                    load_config({"TABDEAL_FUTURES_API": api})


if __name__ == "__main__":
    unittest.main()
