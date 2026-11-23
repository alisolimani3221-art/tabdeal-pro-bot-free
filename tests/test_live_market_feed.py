import unittest

from live_market_feed import LiveMarketPriceFeed


class LiveMarketPriceFeedTests(unittest.TestCase):
    def test_requires_valid_exchange_symbol(self):
        for symbol in ("BTCUSDT", "BTC/USDT", "BTC__USDT", "", "btc_usdt"):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    LiveMarketPriceFeed(symbol, lambda *_: None)

    def test_requires_price_callback(self):
        with self.assertRaisesRegex(ValueError, "on_price"):
            LiveMarketPriceFeed("BTC_USDT", None)

    def test_reconnect_delay_is_bounded(self):
        for delay in (0, 0.1, 61, float("inf")):
            with self.subTest(delay=delay):
                with self.assertRaises(ValueError):
                    LiveMarketPriceFeed("BTC_USDT", lambda *_: None,
                                        reconnect_delay=delay)

    def test_symbol_change_is_validated_and_reports_change(self):
        feed = LiveMarketPriceFeed("BTC_USDT", lambda *_: None)
        self.assertFalse(feed.set_symbol("BTC_USDT"))
        self.assertTrue(feed.set_symbol("ETH_USDT"))
        self.assertEqual(feed.symbol, "ETH_USDT")
        with self.assertRaises(ValueError):
            feed.set_symbol("ETHUSDT")

    def test_stop_before_start_is_safe(self):
        feed = LiveMarketPriceFeed("BTC_USDT", lambda *_: None)
        self.assertTrue(feed.stop())


if __name__ == "__main__":
    unittest.main()
