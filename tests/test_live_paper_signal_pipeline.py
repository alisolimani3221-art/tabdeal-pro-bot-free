"""End-to-end safety tests for validated public trades -> candles -> paper signals.

These tests mirror the production data path without opening a websocket or
using credentials. The paper engine has no live-order capability.
"""
import json
import time
import unittest

from market_candles import TradeCandleBuilder
from paper_trader import PaperTradingEngine, prepare_live_market_snapshot
from risk_analysis import analyze_klines
from multi_timeframe import analyze_multi_timeframe
from trade_stream_probe import parse_trade_message


class LivePaperSignalPipelineTests(unittest.TestCase):
    def test_valid_trade_stream_reaches_closed_candle_analysis_and_paper_engine(self):
        symbol = "BTC_USDT"
        builder = TradeCandleBuilder(symbol, max_candles=2000)
        engine = PaperTradingEngine(capital=8.0, risk_percent=0.5, max_leverage=3)
        first_minute = (int(time.time() * 1000) // 60_000) - (24 * 60 + 2)
        accepted = 0

        # A monotonic rising synthetic stream gives the final two 5m bars
        # and final closed 15m bar unambiguous bullish body confirmation.
        hourly_steps = (1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        price = 100.0
        last_result = None
        opened_result = None
        opened_price = None
        final_parsed = None
        for index in range(24 * 60 + 3):
            hour, minute = divmod(index, 60)
            step = hourly_steps[hour % len(hourly_steps)]
            minute_start = 100.0 + sum(hourly_steps[h % len(hourly_steps)]
                                       for h in range(hour))
            price = minute_start + step * (minute + 1) / 60.0
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
            final_parsed = parsed

        self.assertTrue(builder.is_ready)
        def deterministic_directional_analyzer(candles, interval_ms=60_000):
            # The market-history path and both aggregate windows are real.
            # The 1m categorical signal is neutral (e.g. SMA10/SMA20 have not
            # aligned), while its closed close/RSI still produce a bullish trigger.
            return {
                "signal": "NEUTRAL" if interval_ms == 60_000 else "BULLISH",
                "price": 100.0,
                "sma10": 99.0, "sma20": 98.0,
                "rsi14": 56.0, "atr14": 0.8,
                "closed_candles": len(candles) - 1,
            }

        technical = analyze_multi_timeframe(
            builder.candles_for_analysis(), final_parsed["timestamp_ms"],
            deterministic_directional_analyzer,
        )
        self.assertEqual(technical["timeframe_analysis"]["signals"]["1"], "NEUTRAL")
        self.assertEqual(technical["timeframe_analysis"]["signals"]["5"], "BULLISH")
        self.assertEqual(technical["timeframe_analysis"]["signals"]["15"], "BULLISH")
        self.assertTrue(technical["timeframe_analysis"]["entry_trigger"])
        technical["symbol"] = symbol
        technical = prepare_live_market_snapshot(technical, final_parsed["price"])
        technical["candle_open_time"] = int(builder.closed[-1][0])
        last_result = engine.process_scan([technical])
        if any(event.get("event") == "OPENED" for event in last_result.get("events", [])):
            opened_result = last_result
            opened_price = final_parsed["price"]

        self.assertEqual(accepted, 24 * 60 + 3)
        self.assertTrue(builder.is_ready)
        self.assertEqual(last_result["candidates"][0]["symbol"], symbol)
        self.assertIsNotNone(opened_result, "a valid setup should open a simulated position once")
        self.assertEqual(opened_result["events"][0]["event"], "OPENED")
        self.assertEqual(last_result["open_position"]["symbol"], symbol)
        self.assertAlmostEqual(last_result["open_position"]["entry"], opened_price * (1 + engine.slippage_rate))
        self.assertLessEqual(last_result["open_position"]["leverage"], 3)
        self.assertLessEqual(last_result["open_position"]["risk_limit"], (last_result["cash"] + last_result["open_position"]["fees_entry"]) * 0.005 + 1e-9)
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
        self.assertEqual(len(builder.candles_for_analysis()), 1)
        self.assertFalse(builder.is_ready)


if __name__ == "__main__":
    unittest.main()
