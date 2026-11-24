import os
import tempfile
import unittest

from candle_history import load_candle_cache
from multi_symbol_candle_cache import MultiSymbolCandleCache


class MultiSymbolCandleCacheTests(unittest.TestCase):
    def trade(self, symbol, minute, second=2, price=100):
        return {"symbol": symbol, "timestamp_ms": minute * 60000 + second * 1000,
                "price": price, "quantity": 1.0}

    def test_caches_each_allowed_market_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = MultiSymbolCandleCache(directory, {"BTC_USDT", "ETH_USDT"})
            for minute in range(100, 123):
                cache.add_trade(self.trade("BTC_USDT", minute), (minute + 1) * 60000)
                cache.add_trade(self.trade("ETH_USDT", minute, price=200),
                                (minute + 1) * 60000)
            btc_path = os.path.join(directory, "BTC_USDT.json")
            eth_path = os.path.join(directory, "ETH_USDT.json")
            self.assertTrue(os.path.isfile(btc_path))
            self.assertTrue(os.path.isfile(eth_path))
            btc = load_candle_cache(btc_path, "BTC_USDT", 124 * 60000)
            eth = load_candle_cache(eth_path, "ETH_USDT", 124 * 60000)
            self.assertGreaterEqual(len(btc), 20)
            self.assertGreaterEqual(len(eth), 20)
            self.assertEqual(btc[-1][4], 100.0)
            self.assertEqual(eth[-1][4], 200.0)

    def test_ignores_unlisted_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = MultiSymbolCandleCache(directory, {"BTC_USDT"})
            result = cache.add_trade(self.trade("DOGE_USDT", 100))
            self.assertEqual(result["reason"], "symbol_not_allowed")
            self.assertEqual(os.listdir(directory), [])

    def test_restored_cache_is_invalidated_on_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = MultiSymbolCandleCache(directory, {"BTC_USDT"})
            for minute in range(100, 123):
                cache.add_trade(self.trade("BTC_USDT", minute), (minute + 1) * 60000)
            cache2 = MultiSymbolCandleCache(directory, {"BTC_USDT"})
            result = cache2.add_trade(self.trade("BTC_USDT", 125), 126 * 60000)
            self.assertTrue(result["gap_detected"])
            self.assertEqual(result["closed_candles"], 0)
            self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()
