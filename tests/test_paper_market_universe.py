import unittest

from paper_market_universe import PaperMarketUniverse


BASE_TIME = 1_700_000_000_000


def feed_warmup(universe, symbol, count=22, start_minute=0):
    result = None
    for minute in range(start_minute, start_minute + count):
        result = universe.add_trade({
            "ok": True,
            "symbol": symbol,
            "price": 100.0 + minute * 0.05,
            "quantity": 1.0,
            "timestamp_ms": BASE_TIME + minute * 60_000 + 1_000,
        }, now=float(minute + 1))
    return result


class PaperMarketUniverseTests(unittest.TestCase):
    def test_builds_independent_candidates_for_multiple_symbols(self):
        universe = PaperMarketUniverse(["ETH_USDT", "BTC_USDT", "ETH_USDT"])
        self.assertEqual(universe.symbols, ("BTC_USDT", "ETH_USDT"))

        feed_warmup(universe, "BTC_USDT")
        feed_warmup(universe, "ETH_USDT")

        candidates = universe.candidates(now=30.0)
        self.assertEqual({item["symbol"] for item in candidates},
                         {"BTC_USDT", "ETH_USDT"})
        self.assertTrue(all(item["closed_candles"] == 20 for item in candidates))

    def test_one_market_warmup_does_not_block_another_market(self):
        universe = PaperMarketUniverse(["BTC_USDT", "ETH_USDT"])
        feed_warmup(universe, "BTC_USDT")

        candidates = universe.candidates(now=30.0)
        self.assertEqual([item["symbol"] for item in candidates], ["BTC_USDT"])
        status = universe.status()
        self.assertEqual(status["symbol_count"], 2)
        self.assertEqual(status["ready_count"], 1)

    def test_stale_symbol_is_excluded_without_hiding_fresh_symbols(self):
        universe = PaperMarketUniverse(["BTC_USDT", "ETH_USDT"])
        feed_warmup(universe, "BTC_USDT")
        feed_warmup(universe, "ETH_USDT")

        universe.add_trade({
            "ok": True, "symbol": "ETH_USDT", "price": 105.0,
            "quantity": 1.0, "timestamp_ms": BASE_TIME + 22 * 60_000 + 1_000,
        }, now=23.0)
        candidates = universe.candidates(now=60.0, max_age_seconds=30.0)
        self.assertEqual([item["symbol"] for item in candidates], ["ETH_USDT"])

    def test_gap_invalidates_only_the_affected_market(self):
        universe = PaperMarketUniverse(["BTC_USDT", "ETH_USDT"])
        feed_warmup(universe, "BTC_USDT")
        feed_warmup(universe, "ETH_USDT")

        gap = universe.add_trade({
            "ok": True, "symbol": "BTC_USDT", "price": 102.0,
            "quantity": 1.0, "timestamp_ms": BASE_TIME + 23 * 60_000 + 1_000,
        }, now=24.0)
        self.assertTrue(gap["gap_detected"])
        candidates = universe.candidates(now=25.0)
        self.assertEqual([item["symbol"] for item in candidates], ["ETH_USDT"])

    def test_reconnect_invalidates_live_candidates_until_continuity_returns(self):
        universe = PaperMarketUniverse(["BTC_USDT"])
        feed_warmup(universe, "BTC_USDT")
        self.assertEqual(len(universe.candidates(now=30.0)), 1)

        universe.reset_after_reconnect()
        self.assertEqual(universe.candidates(now=31.0), [])
        status = universe.status()["symbols"]["BTC_USDT"]
        self.assertTrue(status["reconnect_check_pending"])

    def test_unsupported_symbols_are_ignored(self):
        universe = PaperMarketUniverse(["BTC_USDT"])
        result = universe.add_trade({
            "ok": True, "symbol": "UNKNOWN_USDT", "price": 10.0,
            "quantity": 1.0, "timestamp_ms": BASE_TIME,
        }, now=1.0)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "unsupported_symbol")

    def test_rejects_invalid_universe_configuration(self):
        for symbols in ([], ["bad symbol"], [None]):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                PaperMarketUniverse(symbols)
        with self.assertRaises(ValueError):
            PaperMarketUniverse(["BTC_USDT"], max_symbols=0)


if __name__ == "__main__":
    unittest.main()
