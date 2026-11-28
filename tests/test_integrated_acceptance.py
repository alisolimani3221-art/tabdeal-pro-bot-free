"""Cross-module acceptance checks for the paper-only Futures Analyzer.

These tests exercise the five release gates together without credentials,
exchange order endpoints, Android UI state, or live order placement.
"""
import json
import time
import unittest

from market_candles import TradeCandleBuilder
from multi_market_scan import scan_market_universe
from paper_trader import PaperTradingEngine
from risk_analysis import analyze_klines, calculate_position_risk
from trade_stream_probe import parse_trade_message


class IntegratedAcceptanceTests(unittest.TestCase):
    def test_live_trade_to_closed_candles_and_indicator_readiness(self):
        now_ms = int(time.time() * 1000)
        payload = {"trade": {
            "symbol": "BTC_USDT", "price": "60244.6",
            "quantity": "0.01", "timestamp": now_ms,
        }}
        parsed = parse_trade_message(json.dumps(payload), "BTC_USDT")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["symbol"], "BTC_USDT")

        builder = TradeCandleBuilder("BTC_USDT")
        first_minute = (now_ms // 60_000) - 30
        for offset in range(25):
            stamp = (first_minute + offset) * 60_000 + 1_000
            result = builder.add_trade({
                "symbol": "BTC_USDT", "price": 60_000 + offset,
                "quantity": 0.01, "timestamp_ms": stamp,
            })
            self.assertTrue(result["accepted"])
        self.assertTrue(builder.is_ready)
        indicators = analyze_klines(builder.candles_for_analysis())
        self.assertGreaterEqual(indicators["closed_candles"], 23)
        self.assertIn(indicators["signal"], ("BULLISH", "BEARISH", "NEUTRAL"))

    def test_cost_aware_position_risk_respects_half_percent_and_three_x_caps(self):
        result = calculate_position_risk(
            capital=8.0, risk_percent=0.5, entry_price=60_000,
            stop_percent=0.8, leverage=3, reward_risk=2,
            fee_rate=0.0005, slippage_rate=0.0002,
        )
        self.assertLessEqual(result["risk_amount"], result["risk_limit"] + 1e-10)
        self.assertLessEqual(result["leverage"], 3)
        self.assertGreater(result["fees_round_trip_estimate"], 0)
        self.assertLessEqual(result["notional"], 24.0 + 1e-10)

    def test_paper_accounting_history_and_emergency_stop_survive_restore(self):
        candidate = {
            "symbol": "BTC_USDT", "price": 60_000,
            "sma10": 59_900, "sma20": 59_800,
            "rsi14": 56, "atr14": 400, "signal": "BULLISH",
            "closed_candles": 23, "candle_open_time": 60_000,
            "timeframe_analysis": {"ready": True, "agreement": True,
            "direction": "BULLISH", "entry_trigger": True,
            "15m_confirmation_ready": True,
            "entry_trigger_direction": "BULLISH", "required_minutes": [1, 5, 15],
            "closed_candles": {"1": 1439, "5": 288, "15": 96},
            "signals": {
                "1": "BULLISH", "5": "BULLISH", "15": "BULLISH"},
            "risk_range_by_timeframe": {"5": 800.0}},
        }
        engine = PaperTradingEngine(capital=8.0, risk_percent=0.5,
                                    max_leverage=3, fee_rate=0.0005,
                                    slippage_rate=0.0002)
        opened = engine.process_scan([candidate])
        self.assertIsNotNone(opened["open_position"])
        self.assertLessEqual(opened["open_position"]["risk_budget"], 0.04 + 1e-10)

        stopped = engine.emergency_close("acceptance-test emergency stop")
        self.assertEqual(stopped["event"], "CLOSED")
        self.assertEqual(engine.closed_trades[-1]["reason"], "EMERGENCY_STOP")
        trade = engine.closed_trades[-1]
        self.assertAlmostEqual(
            trade["net_pnl"],
            trade["gross_pnl"] - trade["fees_entry"] - trade["fees_exit"],
        )

        restored = PaperTradingEngine.restore_state(engine.export_state())
        self.assertTrue(restored.halted)
        self.assertEqual(len(restored.closed_trades), 1)
        self.assertEqual(restored.halt_reason, "EMERGENCY_STOP")
        rearmed = restored.rearm_after_operator_stop()
        self.assertTrue(rearmed["rearmed"])
        self.assertFalse(rearmed["live_order_submitted"])

    def test_full_market_scan_is_read_only_and_accounts_for_each_symbol(self):
        symbols = ["BTC_USDT", "ETH_USDT", "XRP_USDT"]

        def fake_fetch(path, params):
            if path == "/exchangeInfo":
                return symbols
            symbol = params["symbol"]
            offset = symbols.index(symbol) * 10
            return {
                "bids": [[str(100 + offset), "2"]],
                "asks": [[str(100.2 + offset), "3"]],
            }

        report = scan_market_universe(fetch_json=fake_fetch, max_workers=3)
        self.assertEqual(report["active_count"], len(symbols))
        self.assertEqual(report["success_count"], len(symbols))
        self.assertEqual(report["error_count"], 0)
        self.assertFalse(report["technical_signals"])
        self.assertFalse(report["live_orders"])
        self.assertEqual(
            {row["symbol"] for row in report["snapshots"]}, set(symbols)
        )


if __name__ == "__main__":
    unittest.main()
