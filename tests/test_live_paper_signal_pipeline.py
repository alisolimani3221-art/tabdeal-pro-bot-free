"""End-to-end safety tests for validated public trades -> candles -> paper signals.

These tests mirror the production data path without opening a websocket or
using credentials. The paper engine has no live-order capability.
"""
import json
import time
import unittest

from market_candles import TradeCandleBuilder
from paper_trader import PaperTradingEngine
from risk_analysis import analyze_klines
from trade_stream_probe import parse_trade_message


class LivePaperSignalPipelineTests(unittest.TestCase):
    def test_valid_trade_stream_reaches_closed_candle_analysis_and_paper_engine(self):
        symbol = "BTC_USDT"
        builder = TradeCandleBuilder(symbol)
        engine = PaperTradingEngine(capital=8.0, risk_percent=0.5, max_leverage=3)
        first_minute = (int(time.time() * 1000) // 60_000) - 54
        accepted = 0

        # Alternating +0.30/-0.20 closes produce a rising trend with RSI near 60.
        price = 100.0
        last_result = None
        for index in range(53):
            if index:
                price += 0.30 if index % 2 else -0.20
            timestamp_ms = (first_minute + index) * 60_000 + 1_000
            raw = json.dumps({"trade": {
                "symbol": symbol, "price": str(price), "amount": "1",
                "updated": timestamp_ms,
            }})
            parsed = parse_trade_message(raw, expected_symbol=symbol)
            self.assertTrue(parsed["ok"], parsed)
            update = builder.add_trade(parsed)
            self.assertTrue(update["accepted"], update)
            accepted += 1
            if builder.is_ready:
                technical = analyze_klines(builder.candles_for_analysis())
                technical["symbol"] = symbol
                last_result = engine.process_scan([technical])

        self.assertEqual(accepted, 53)
        self.assertTrue(builder.is_ready)
        self.assertEqual(last_result["candidates"][0]["symbol"], symbol)
        self.assertEqual(last_result["events"][0]["event"], "OPENED")
        self.assertEqual(last_result["open_position"]["symbol"], symbol)
        self.assertLessEqual(last_result["open_position"]["leverage"], 3)
        self.assertLessEqual(last_result["open_position"]["risk_limit"], 0.04)
        self.assertFalse(hasattr(engine, "create_order"))
        self.assertFalse(hasattr(engine, "new_order"))

    def test_order_book_payload_never_becomes_a_trade_or_signal(self):
        builder = TradeCandleBuilder("BTC_USDT")
        order_book = json.dumps({
            "bids": [["100.0", "2.0"]],
            "asks": [["100.1", "3.0"]],
            "symbol": "BTC_USDT",
        })
        parsed = parse_trade_message(order_book, expected_symbol="BTC_USDT")
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["reason"], "missing_trade_object")
        self.assertEqual(builder.status()["rejected_trades"], 0)
        self.assertFalse(builder.is_ready)

    def test_invalid_trade_and_symbol_mismatch_cannot_enter_candle_builder(self):
        builder = TradeCandleBuilder("BTC_USDT")
        timestamp = int(time.time())
        invalid = parse_trade_message(json.dumps({"trade": {
            "symbol": "BTC_USDT", "price": "NaN", "amount": "1",
            "timestamp": timestamp,
        }}), expected_symbol="BTC_USDT")
        mismatch = parse_trade_message(json.dumps({"trade": {
            "symbol": "ETH_USDT", "price": "100", "amount": "1",
            "timestamp": timestamp,
        }}), expected_symbol="BTC_USDT")
        self.assertFalse(invalid["ok"])
        self.assertEqual(mismatch["reason"], "symbol_mismatch")
        self.assertEqual(builder.status()["rejected_trades"], 0)
        self.assertFalse(builder.is_ready)

    def test_gap_clears_warmup_and_blocks_analysis_until_rebuilt(self):
        builder = TradeCandleBuilder("BTC_USDT")
        base = (int(time.time() * 1000) // 60_000) - 60
        for minute in range(53):
            result = builder.add_trade({
                "symbol": "BTC_USDT", "price": 100.0 + minute * 0.01,
                "quantity": 1.0, "timestamp_ms": (base + minute) * 60_000 + 1000,
            })
            self.assertTrue(result["accepted"])
        self.assertTrue(builder.is_ready)

        gap = builder.add_trade({
            "symbol": "BTC_USDT", "price": 101.0, "quantity": 1.0,
            "timestamp_ms": (base + 55) * 60_000 + 1000,
        })
        self.assertTrue(gap["gap_detected"])
        self.assertFalse(builder.is_ready)
        self.assertEqual(builder.status()["closed_candles"], 0)
        self.assertEqual(builder.candles_for_analysis(), [])


if __name__ == "__main__":
    unittest.main()
