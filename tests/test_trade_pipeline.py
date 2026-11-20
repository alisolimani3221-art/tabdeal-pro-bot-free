import json
import time
import unittest

from market_candles import TradeCandleBuilder
from risk_analysis import analyze_klines
from trade_stream_probe import parse_trade_message


class TradeToSignalPipelineTests(unittest.TestCase):
    def test_valid_broadcast_trades_warm_up_candles_before_analysis(self):
        builder = TradeCandleBuilder("BTC_USDT")
        base_minute = int(time.time() // 60) - 60
        # 22 consecutive one-minute buckets: first partial bucket is discarded,
        # then 20 closed candles plus one forming candle become available.
        for minute in range(53):
            price = 100.0 + minute * 0.05
            message = json.dumps({
                "trade": {
                    "symbol": "BTC_USDT",
                    "price": price,
                    "quantity": 1.0,
                    "timestamp": (base_minute + minute) * 60_000 + 5_000,
                    "sequence": minute + 1,
                }
            })
            parsed = parse_trade_message(message, expected_symbol="BTC_USDT")
            self.assertTrue(parsed["ok"], parsed)
            result = builder.add_trade(parsed)
            self.assertTrue(result["accepted"], result)

            if minute < 21:
                self.assertFalse(builder.is_ready)

        self.assertTrue(builder.is_ready)
        self.assertEqual(builder.status()["closed_candles"], 51)
        technical = analyze_klines(builder.candles_for_analysis())
        self.assertEqual(technical["closed_candles"], 51)
        self.assertIn(technical["signal"], ("BULLISH", "BEARISH", "NEUTRAL"))
        self.assertGreater(technical["price"], 0)

    def test_stream_pipeline_does_not_turn_orderbook_payload_into_trade(self):
        builder = TradeCandleBuilder("BTC_USDT")
        payload = json.dumps({
            "bids": [["100", "2"]],
            "asks": [["101", "3"]],
        })
        parsed = parse_trade_message(payload, expected_symbol="BTC_USDT")
        self.assertFalse(parsed["ok"])
        result = builder.add_trade(parsed)
        self.assertFalse(result["accepted"])
        self.assertEqual(builder.status()["closed_candles"], 0)


if __name__ == "__main__":
    unittest.main()
