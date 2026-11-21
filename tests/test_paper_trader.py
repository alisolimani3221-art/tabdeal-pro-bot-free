import unittest
from paper_trader import PaperTradingEngine, rank_signal


def setup(symbol="AAA_USDT", price=100.0, signal="BULLISH"):
    if signal == "BULLISH":
        return {"symbol": symbol, "price": price, "sma20": 99, "sma50": 98,
                "rsi14": 56, "atr14": 0.8, "signal": signal}
    return {"symbol": symbol, "price": price, "sma20": 101, "sma50": 102,
            "rsi14": 44, "atr14": 0.8, "signal": signal}


class PaperTraderTests(unittest.TestCase):
    def test_rejects_neutral_or_extreme_volatility(self):
        self.assertIsNone(rank_signal(dict(setup(), signal="NEUTRAL")))
        self.assertIsNone(rank_signal(dict(setup(), atr14=3.0)))

    def test_opens_only_paper_position_with_eight_dollar_capital(self):
        engine = PaperTradingEngine(capital=8)
        result = engine.process_scan([setup()])
        self.assertEqual(result["events"][0]["event"], "OPENED")
        self.assertEqual(result["open_position"]["symbol"], "AAA_USDT")
        self.assertLessEqual(result["open_position"]["notional"] /
                             result["open_position"]["leverage"], 2.0)
        self.assertLessEqual(result["open_position"]["risk_budget"], 0.04)

    def test_stop_closes_position_and_records_net_result(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        result = engine.process_scan([dict(setup(price=98.0), signal="NEUTRAL")])
        self.assertTrue(any(e["event"] == "CLOSED" for e in result["events"]))
        self.assertIsNone(result["open_position"])
        self.assertEqual(engine.closed_trades[-1]["reason"], "STOP")
        self.assertAlmostEqual(engine.closed_trades[-1]["net_pnl"],
                               engine.closed_trades[-1]["gross_pnl"]
                               - engine.closed_trades[-1]["fees_entry"]
                               - engine.closed_trades[-1]["fees_exit"])

    def test_target_closes_long_position(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        result = engine.process_scan([dict(setup(price=103.0), signal="NEUTRAL")])
        self.assertEqual(result["events"][0]["event"], "CLOSED")
        self.assertEqual(result["events"][0]["trade"]["reason"], "TARGET")

    def test_long_stop_triggers_on_intrabar_low_even_when_close_recovers(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        candle = dict(setup(price=100.5), signal="NEUTRAL",
                      candle_open=100.5, candle_high=101.0, candle_low=98.0)
        result = engine.process_scan([candle])
        self.assertEqual(result["events"][0]["trade"]["reason"], "STOP")
        self.assertAlmostEqual(result["events"][0]["trade"]["exit"],
                               result["events"][0]["trade"]["stop"])

    def test_stop_wins_when_stop_and_target_are_both_touched(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        candle = dict(setup(price=100.5), signal="NEUTRAL",
                      candle_open=100.5, candle_high=104.0, candle_low=98.0)
        result = engine.process_scan([candle])
        self.assertEqual(result["events"][0]["trade"]["reason"], "STOP")

    def test_short_stop_triggers_on_intrabar_high(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup(signal="BEARISH")])
        candle = dict(setup(price=100.5, signal="BEARISH"), signal="NEUTRAL",
                      candle_open=100.5, candle_high=103.0, candle_low=99.0)
        result = engine.process_scan([candle])
        self.assertEqual(result["events"][0]["trade"]["reason"], "STOP")

    def test_stop_closes_short_position(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup(signal="BEARISH")])
        result = engine.process_scan([dict(setup(price=102.0, signal="BEARISH"),
                                           signal="NEUTRAL")])
        self.assertEqual(result["events"][0]["trade"]["reason"], "STOP")

    def test_target_closes_short_position(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup(signal="BEARISH")])
        result = engine.process_scan([dict(setup(price=97.0, signal="BEARISH"),
                                           signal="NEUTRAL")])
        self.assertEqual(result["events"][0]["trade"]["reason"], "TARGET")

    def test_does_not_reopen_on_same_scan_after_stop(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        result = engine.process_scan([setup(price=98.0)])
        self.assertTrue(any(e["event"] == "CLOSED" for e in result["events"]))
        self.assertFalse(any(e["event"] == "OPENED" for e in result["events"]))
        self.assertIsNone(result["open_position"])

    def test_same_signal_candle_does_not_reopen_after_intrabar_stop(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        candidate = dict(setup(), signal_key=12345)
        engine.process_scan([candidate])
        stopped = dict(setup(), signal_key=12345, candle_open=100.0,
                       candle_high=101.0, candle_low=98.0)
        result = engine.process_scan([stopped])
        self.assertEqual(result["events"][0]["trade"]["reason"], "STOP")
        self.assertIsNone(result["open_position"])
        repeated = engine.process_scan([candidate])
        self.assertFalse(any(event["event"] == "OPENED" for event in repeated["events"]))
        self.assertIsNone(repeated["open_position"])

    def test_entry_uses_live_mark_not_stale_closed_candle_price(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        candidate = dict(setup(), mark_price=101.0, signal_key=12345)
        result = engine.process_scan([candidate])
        self.assertEqual(result["events"][0]["event"], "OPENED")
        self.assertAlmostEqual(result["open_position"]["entry"], 101.0)

    def test_entry_fee_does_not_consume_required_margin(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.01, slippage_rate=0)
        result = engine.process_scan([setup()])
        if result["open_position"]:
            self.assertLessEqual(result["open_position"]["notional"] /
                                 result["open_position"]["leverage"],
                                 result["cash"])

    def test_rejects_volatility_above_two_percent(self):
        self.assertIsNone(rank_signal(dict(setup(), atr14=2.1)))

    def test_rejects_non_usdt_symbol(self):
        self.assertIsNone(rank_signal(setup(symbol="BTC_BTC")))

    def test_rejects_malformed_and_empty_symbols(self):
        for symbol in ("", "USDT", "AAA-USDT", "AAA/USDT", "_USDT", "AAA__USDT", "A_AAA_USDT", "AAA USDT"):
            with self.subTest(symbol=symbol):
                self.assertIsNone(rank_signal(setup(symbol=symbol)))

    def test_rejects_non_list_scan_input_safely(self):
        engine = PaperTradingEngine(capital=8)
        result = engine.process_scan(None)
        self.assertEqual(result["events"], [])
        self.assertIsNone(result["open_position"])

    def test_rejects_fractional_max_leverage(self):
        with self.assertRaises(ValueError):
            PaperTradingEngine(capital=8, max_leverage=2.5)

    def test_rejects_candidate_without_symbol(self):
        candidate = setup()
        candidate.pop("symbol")
        self.assertIsNone(rank_signal(candidate))

    def test_one_position_at_a_time(self):
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([setup()])
        result = engine.process_scan([setup("BBB_USDT"), setup("CCC_USDT")])
        self.assertEqual(result["open_position"]["symbol"], "AAA_USDT")

    def test_risk_and_leverage_caps_are_enforced(self):
        with self.assertRaises(ValueError):
            PaperTradingEngine(capital=8, risk_percent=1)
        with self.assertRaises(ValueError):
            PaperTradingEngine(capital=8, max_leverage=5)

    def test_rejects_invalid_capital_and_risk_parameters(self):
        for kwargs in ({"capital": 0}, {"capital": True}, {"risk_percent": 0},
                       {"risk_percent": float("nan")}, {"fee_rate": 0},
                       {"fee_rate": 0.06}, {"slippage_rate": -0.1},
                       {"slippage_rate": 0.02}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((ValueError, TypeError)):
                    PaperTradingEngine(**kwargs)

    def test_rejects_boolean_and_nonfinite_candidate_values(self):
        for field, value in (("price", True), ("sma20", float("inf")),
                             ("sma50", float("nan")), ("atr14", False),
                             ("rsi14", True), ("rsi14", float("inf"))):
            with self.subTest(field=field, value=value):
                self.assertIsNone(rank_signal(dict(setup(), **{field: value})))

    def test_rejects_missing_technical_fields(self):
        for field in ("price", "sma20", "sma50", "rsi14", "atr14"):
            candidate = setup()
            candidate.pop(field)
            with self.subTest(field=field):
                self.assertIsNone(rank_signal(candidate))

    def test_candidate_ordering_prefers_higher_score(self):
        engine = PaperTradingEngine(capital=8)
        result = engine.process_scan([setup("AAA_USDT"), setup("BBB_USDT", price=100)])
        self.assertTrue(result["candidates"])
        self.assertEqual(result["open_position"]["symbol"],
                         result["candidates"][0]["symbol"])


    def test_emergency_stop_closes_open_paper_position_at_last_mark(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        last_mark = engine.position["last_price"]
        result = engine.emergency_close()
        self.assertEqual(result["event"], "CLOSED")
        self.assertEqual(result["trade"]["reason"], "EMERGENCY_STOP")
        self.assertEqual(result["trade"]["exit"], last_mark)
        self.assertIsNone(engine.position)
        self.assertAlmostEqual(
            result["trade"]["net_pnl"],
            result["trade"]["gross_pnl"] - result["trade"]["fees_entry"] - result["trade"]["fees_exit"]
        )

    def test_emergency_stop_with_no_position_is_idempotent(self):
        engine = PaperTradingEngine(capital=8)
        self.assertEqual(engine.emergency_close(), {"event": "STOPPED", "trade": None})
        self.assertEqual(engine.emergency_close(), {"event": "STOPPED", "trade": None})


    def test_emergency_stop_permanently_blocks_later_entries(self):
        engine = PaperTradingEngine(capital=8)
        engine.emergency_close()
        result = engine.process_scan([setup()])
        self.assertTrue(result["halted"])
        self.assertIsNone(result["open_position"])
        self.assertEqual(result["events"], [])

    def test_engine_never_exposes_live_order_method(self):
        engine = PaperTradingEngine(capital=8)
        self.assertFalse(hasattr(engine, "create_order"))
        self.assertFalse(hasattr(engine, "new_order"))


if __name__ == "__main__":
    unittest.main()
