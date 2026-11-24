import json
import os
import tempfile
import unittest

from paper_trade_history import load_paper_history, summarize_paper_trades
from paper_trader import PaperTradingEngine


def signal(price=100.0, side="BULLISH"):
    if side == "BULLISH":
        return {"symbol": "AAA_USDT", "side": side, "price": price,
                "sma10": 99, "sma20": 98, "rsi14": 56, "atr14": 0.8,
                "signal": side, "closed_candles": 23, "candle_open_time": 60000}
    return {"symbol": "BBB_USDT", "side": side, "price": price,
            "sma10": 101, "sma20": 102, "rsi14": 44, "atr14": 0.8,
            "signal": side, "closed_candles": 23, "candle_open_time": 120000}


class PaperTradeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "paper-engine-state.json")

    def tearDown(self):
        self.temp.cleanup()

    def _closed_engine(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([signal()])
        engine.process_scan([dict(signal(price=98), signal="NEUTRAL")])
        self.assertEqual(len(engine.closed_trades), 1)
        return engine

    def test_empty_history_when_no_persisted_paper_state(self):
        result = load_paper_history(self.path)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["net_pnl"], 0)
        self.assertEqual(result["trades"], [])

    def test_summary_counts_profit_loss_and_multiple_symbols(self):
        trades = [
            {"symbol": "AAA_USDT", "side": "BULLISH", "net_pnl": 0.12},
            {"symbol": "BBB_USDT", "side": "BEARISH", "net_pnl": -0.04},
            {"symbol": "CCC_USDT", "side": "BULLISH", "net_pnl": 0.0},
        ]
        result = summarize_paper_trades(trades)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 1)
        self.assertAlmostEqual(result["net_pnl"], 0.08)
        self.assertEqual({row["symbol"] for row in result["trades"]},
                         {"AAA_USDT", "BBB_USDT", "CCC_USDT"})

    def test_reads_only_validated_paper_engine_storage(self):
        engine = self._closed_engine()
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump(engine.export_state(), stream)
        result = load_paper_history(self.path)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["trades"][0]["symbol"], "AAA_USDT")
        self.assertAlmostEqual(result["net_pnl"],
                               engine.closed_trades[0]["net_pnl"])

    def test_reads_live_engine_snapshot_without_mutating_it(self):
        engine = self._closed_engine()
        before = engine.export_state()
        result = load_paper_history(self.path, engine=engine)
        self.assertEqual(result["count"], 1)
        self.assertEqual(engine.export_state(), before)
        self.assertFalse(engine.halted)
        self.assertIsNone(engine.position)

    def test_corrupt_or_non_paper_state_fails_closed(self):
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump({"schema_version": 99}, stream)
        with self.assertRaises(ValueError):
            load_paper_history(self.path)

    def test_live_history_is_not_queried_or_mixed(self):
        # The reader accepts only the app's paper-engine state path/engine;
        # no account, exchange API, or live-order history is consulted.
        result = load_paper_history(self.path)
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
