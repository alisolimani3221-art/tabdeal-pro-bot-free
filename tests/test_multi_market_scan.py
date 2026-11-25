import unittest

from multi_market_scan import (extract_active_symbols, load_active_market_symbols,
                               scan_market_universe)


class MultiMarketScanTests(unittest.TestCase):
    def test_loads_complete_active_symbol_universe_from_exchange_info(self):
        calls = []
        def fake_fetch(path, params):
            calls.append((path, params))
            return {"symbols": [
                {"symbol": "BTC_USDT", "quoteAsset": "USDT", "status": "TRADING",
                 "contractType": "PERPETUAL"},
                {"symbol": "ETH_USDT", "quoteAsset": "USDT", "status": "TRADING",
                 "contractType": "PERPETUAL"},
                {"symbol": "OLD_USDT", "quoteAsset": "USDT", "status": "BREAK"},
                {"symbol": "BTC_BTC", "quoteAsset": "BTC", "status": "TRADING"},
            ]}
        self.assertEqual(load_active_market_symbols(fake_fetch),
                         ["BTC_USDT", "ETH_USDT"])
        self.assertEqual(calls, [("/exchangeInfo", None)])

    def test_symbol_universe_fails_closed_when_exchange_returns_no_active_symbols(self):
        with self.assertRaises(RuntimeError):
            load_active_market_symbols(lambda *_: {"symbols": []})

    def test_extracts_exchange_symbols_from_supported_envelopes(self):
        payload = {"data": {"symbols": [
            {"symbol": "BTC_USDT", "quoteAsset": "USDT", "status": "TRADING",
             "contractType": "PERPETUAL"},
            {"symbol": "ETH_USDT", "quoteAsset": "USDT", "status": "TRADING",
             "contractType": "PERPETUAL"},
            {"symbol": "BAD_BTC", "quoteAsset": "BTC", "status": "TRADING"},
        ]}}
        self.assertEqual(extract_active_symbols(payload), ["BTC_USDT", "ETH_USDT"])

    def test_scans_all_symbols_and_ranks_tighter_spreads_first(self):
        def fake_fetch(path, params):
            if path == "/exchangeInfo":
                return ["AAA_USDT", "BBB_USDT", "CCC_USDT"]
            symbol = params["symbol"]
            if symbol == "AAA_USDT":
                return {"bids": [["99.9", "2"]], "asks": [["100.1", "3"]]}
            if symbol == "BBB_USDT":
                return {"bids": [["99", "2"]], "asks": [["101", "3"]]}
            return {"bids": [["49.9", "2"]], "asks": [["50.1", "3"]]}
        result = scan_market_universe(fetch_json=fake_fetch, max_workers=3)
        self.assertEqual(result["active_count"], 3)
        self.assertEqual(result["success_count"], 3)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["snapshots"][0]["symbol"], "AAA_USDT")
        self.assertEqual(result["snapshots"][1]["symbol"], "CCC_USDT")
        self.assertFalse(result["technical_signals"])
        self.assertFalse(result["live_orders"])

    def test_keeps_per_symbol_errors_without_losing_successful_markets(self):
        def fake_fetch(path, params):
            if path == "/exchangeInfo":
                return ["AAA_USDT", "BBB_USDT"]
            if params["symbol"] == "BBB_USDT":
                raise RuntimeError("temporary network error")
            return {"bids": [["99", "2"]], "asks": [["101", "3"]]}
        result = scan_market_universe(fetch_json=fake_fetch)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["errors"][0]["symbol"], "BBB_USDT")

    def test_rejects_invalid_concurrency(self):
        for workers in (0, 9, True, 1.5):
            with self.subTest(workers=workers):
                with self.assertRaises(ValueError):
                    scan_market_universe(fetch_json=lambda *_: [], max_workers=workers)

    def test_empty_universe_fails_closed(self):
        with self.assertRaises(RuntimeError):
            scan_market_universe(fetch_json=lambda *_: [])


    def test_retries_transient_depth_failure_then_validates_book(self):
        attempts = {"AAA_USDT": 0}
        def fake_fetch(path, params):
            if path == "/exchangeInfo":
                return ["AAA_USDT"]
            attempts[params["symbol"]] += 1
            if attempts[params["symbol"]] == 1:
                raise TimeoutError("temporary timeout")
            return {"bids": [["99", "2"]], "asks": [["101", "3"]]}
        result = scan_market_universe(fetch_json=fake_fetch, max_workers=1)
        self.assertEqual(attempts["AAA_USDT"], 2)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
