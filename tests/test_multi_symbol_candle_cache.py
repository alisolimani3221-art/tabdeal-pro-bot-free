import os
import tempfile
import unittest

from candle_history import load_candle_cache
from multi_symbol_candle_cache import MultiSymbolCandleCache


class MultiSymbolCandleCacheTests(unittest.TestCase):
    def trade(self, symbol, minute, second=2, price=100):
        return {"symbol": symbol, "timestamp_ms": minute * 60000 + second * 1000,
                "price": price, "quantity": 1.0}

    def test_caches_five_allowed_markets_independently_across_restart(self):
        symbols = {"BTC_USDT", "ETH_USDT", "XRP_USDT", "BNB_USDT", "SOL_USDT"}
        prices = {
            "BTC_USDT": 100.0, "ETH_USDT": 200.0, "XRP_USDT": 300.0,
            "BNB_USDT": 400.0, "SOL_USDT": 500.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = MultiSymbolCandleCache(directory, symbols)
            for minute in range(100, 123):
                for symbol, price in prices.items():
                    cache.add_trade(
                        self.trade(symbol, minute, price=price),
                        (minute + 1) * 60000,
                    )
            for symbol in symbols:
                self.assertTrue(os.path.isfile(os.path.join(directory, symbol + ".json")))

            # Simulate a process restart: a fresh cache instance must restore
            # each symbol's own contiguous closed history, never cross-assign.
            restored = MultiSymbolCandleCache(directory, symbols)
            for symbol, price in prices.items():
                path = os.path.join(directory, symbol + ".json")
                rows = load_candle_cache(path, symbol, 124 * 60000)
                self.assertGreaterEqual(len(rows), 20)
                self.assertEqual(rows[-1][4], price)
                result = restored.add_trade(
                    self.trade(symbol, 123, second=2, price=price),
                    124 * 60000,
                )
                self.assertTrue(result["accepted"])
                self.assertFalse(result["gap_detected"])
                self.assertGreaterEqual(result["closed_candles"], 20)

            # A second instance must not have mixed symbol histories.
            for symbol, price in prices.items():
                self.assertEqual(
                    restored.builders[symbol].closed[-1][4], price
                )

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
            active_path = os.path.join(directory, "BTC_USDT.json")
            self.assertFalse(os.path.exists(active_path))
            archives = [
                name for name in os.listdir(directory)
                if name.startswith("BTC_USDT.json.archive-")
            ]
            self.assertEqual(len(archives), 1)
            archived_path = os.path.join(directory, archives[0])
            archived = load_candle_cache(
                archived_path, "BTC_USDT", 126 * 60000
            )
            self.assertGreaterEqual(len(archived), 20)


if __name__ == "__main__":
    unittest.main()
