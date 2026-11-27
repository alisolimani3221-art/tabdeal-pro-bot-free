import json
import os
import tempfile
import unittest

from services.marketkeep import parse_service_config


class BackgroundMarketServiceConfigTests(unittest.TestCase):
    def test_accepts_only_app_private_archive_path(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "closed-candles.sqlite3")
            raw = json.dumps({"symbol": "BTC_USDT", "archive_path": archive})
            result = parse_service_config(raw, directory)
            self.assertEqual(result["symbol"], "BTC_USDT")
            self.assertEqual(result["archive_path"], os.path.realpath(archive))

    def test_rejects_path_outside_app_private_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = json.dumps({
                "symbol": "BTC_USDT",
                "archive_path": os.path.join(os.path.dirname(directory), "outside.sqlite3"),
            })
            with self.assertRaises(ValueError):
                parse_service_config(raw, directory)

    def test_rejects_bad_symbols_and_malformed_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "closed-candles.sqlite3")
            for symbol in ("BTC/USDT", "BTC__USDT", "", 12, True):
                raw = json.dumps({"symbol": symbol, "archive_path": archive})
                with self.subTest(symbol=symbol):
                    with self.assertRaises(ValueError):
                        parse_service_config(raw, directory)
            for raw in ("", "not-json", "[]", " " * 4097):
                with self.subTest(raw=raw[:20]):
                    with self.assertRaises(ValueError):
                        parse_service_config(raw, directory)


if __name__ == "__main__":
    unittest.main()
