import unittest
from paper_trader import (PaperTradingEngine, rank_signal, diagnose_signal,
                          prepare_live_market_snapshot, PAPER_SCAN_INTERVAL_SECONDS,
                          paper_scan_due)


def setup(symbol="AAA_USDT", price=100.0, signal="BULLISH"):
    if signal == "BULLISH":
        return {"symbol": symbol, "price": price, "sma10": 99, "sma20": 98,
                "rsi14": 56, "atr14": 0.8, "signal": signal,
                "closed_candles": 20, "candle_open_time": 60_000}
    return {"symbol": symbol, "price": price, "sma10": 101, "sma20": 102,
            "rsi14": 44, "atr14": 0.8, "signal": signal,
            "closed_candles": 20, "candle_open_time": 60_000}


class PaperTraderTests(unittest.TestCase):
    def test_paper_scan_scheduler_uses_five_second_interval(self):
        self.assertEqual(PAPER_SCAN_INTERVAL_SECONDS, 5.0)
        self.assertTrue(paper_scan_due(100.0, 0.0))
        self.assertFalse(paper_scan_due(104.999, 100.0))
        self.assertTrue(paper_scan_due(105.0, 100.0))
        self.assertFalse(paper_scan_due(104.0, 100.0))

    def test_paper_scan_scheduler_rejects_invalid_clock_values(self):
        for now, previous in ((True, 1), (float('nan'), 1), (1, float('inf')), (-1, 0)):
            with self.subTest(now=now, previous=previous):
                with self.assertRaises(ValueError):
                    paper_scan_due(now, previous)

    def test_live_snapshot_uses_current_trade_and_discards_pre_entry_candle_range(self):
        original = dict(setup(), candle_open=99.0, candle_high=110.0, candle_low=90.0)
        snapshot = prepare_live_market_snapshot(original, 102.5)
        self.assertEqual(snapshot["price"], 102.5)
        self.assertEqual(snapshot["candle_open"], 102.5)
        self.assertEqual(snapshot["candle_high"], 102.5)
        self.assertEqual(snapshot["candle_low"], 102.5)
        self.assertEqual(snapshot["sma20"], original["sma20"])
        self.assertEqual(snapshot["candle_open_time"], original["candle_open_time"])
        self.assertEqual(original["candle_high"], 110.0)

    def test_live_snapshot_rejects_invalid_current_trade_price(self):
        for value in (None, True, 0, -1, float("nan"), float("inf"), "bad"):
            with self.subTest(live_price=value):
                with self.assertRaises(ValueError):
                    prepare_live_market_snapshot(setup(), value)

    def test_entry_diagnostics_explain_neutral_and_invalid_setups(self):
        self.assertIn("خنثی", diagnose_signal(dict(setup(), signal="NEUTRAL")))
        self.assertIn("RSI", diagnose_signal(dict(setup(), rsi14=75)))
        self.assertIn("نوسان", diagnose_signal(dict(setup(), atr14=3.0)))
        self.assertIn("تأیید شده", diagnose_signal(setup()))

    def test_rejects_signal_without_minimum_closed_candle_provenance(self):
        for value in (None, 0, 19, 19.9, True, "20"):
            with self.subTest(closed_candles=value):
                self.assertIsNone(rank_signal(dict(setup(), closed_candles=value)))

    def test_accepts_signal_with_minimum_closed_candle_provenance(self):
        self.assertIsNotNone(rank_signal(setup()))
        self.assertIsNotNone(rank_signal(dict(setup(), closed_candles=21)))

    def test_rejects_signal_without_valid_closed_candle_timestamp(self):
        for value in (None, 0, -1, True, "60000"):
            with self.subTest(candle_open_time=value):
                self.assertIsNone(rank_signal(dict(setup(), candle_open_time=value)))

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

    def test_does_not_reopen_on_same_candle_after_stop_across_scans(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        result = engine.process_scan([setup(price=98.0)])
        self.assertTrue(any(e["event"] == "CLOSED" for e in result["events"]))
        self.assertFalse(any(e["event"] == "OPENED" for e in result["events"]))
        self.assertIsNone(result["open_position"])
        repeated = engine.process_scan([setup(price=100.0)])
        self.assertFalse(any(e["event"] == "OPENED" for e in repeated["events"]))
        self.assertIsNone(repeated["open_position"])

    def test_allows_entry_on_a_new_candle_after_previous_candle_entry(self):
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([setup()])
        engine.process_scan([setup(price=98.0)])
        result = engine.process_scan([dict(setup(), candle_open_time=120_000)])
        self.assertTrue(any(e["event"] == "OPENED" for e in result["events"]))

    def test_last_entry_candle_survives_engine_restore(self):
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([setup()])
        restored = PaperTradingEngine.restore_state(engine.export_state())
        self.assertEqual(restored.last_entry_candle,
                         {"symbol": "AAA_USDT", "open_time": 60_000})
        repeated = restored.process_scan([setup()])
        self.assertFalse(any(e["event"] == "OPENED" for e in repeated["events"]))

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
        for field, value in (("price", True), ("sma10", float("inf")),
                             ("sma20", float("nan")), ("atr14", False),
                             ("rsi14", True), ("rsi14", float("inf"))):
            with self.subTest(field=field, value=value):
                self.assertIsNone(rank_signal(dict(setup(), **{field: value})))

    def test_rejects_missing_technical_fields(self):
        for field in ("price", "sma10", "sma20", "rsi14", "atr14"):
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

    def test_emergency_stop_detail_survives_restart_and_clears_on_rearm(self):
        engine = PaperTradingEngine(capital=8)
        engine.emergency_close("periodic scan failure: RuntimeError")
        saved = engine.export_state()
        self.assertEqual(saved["schema_version"], 3)
        restored = PaperTradingEngine.restore_state(saved)
        self.assertEqual(
            restored.halt_detail, "periodic scan failure: RuntimeError"
        )
        restored.rearm_after_operator_stop()
        self.assertIsNone(restored.halt_detail)
        self.assertFalse(restored.halted)

    def test_restore_remains_compatible_with_schema_v1_and_v2(self):
        engine = PaperTradingEngine(capital=8)
        current = engine.export_state()
        version_two = dict(current)
        version_two["schema_version"] = 2
        version_two.pop("halt_detail")
        restored_two = PaperTradingEngine.restore_state(version_two)
        self.assertIsNone(restored_two.halt_detail)

        version_one = dict(version_two)
        version_one["schema_version"] = 1
        version_one.pop("last_entry_candle")
        restored_one = PaperTradingEngine.restore_state(version_one)
        self.assertIsNone(restored_one.halt_detail)

        # Legacy halted files must migrate to schema v3 without losing
        # their original reason or becoming unrestorable on the next launch.
        for legacy in (version_one, version_two):
            legacy_halted = dict(legacy)
            legacy_halted["halted"] = True
            legacy_halted["halt_reason"] = "EMERGENCY_STOP"
            migrated = PaperTradingEngine.restore_state(legacy_halted)
            self.assertIn("EMERGENCY_STOP", migrated.halt_detail)
            upgraded_snapshot = migrated.export_state()
            self.assertEqual(upgraded_snapshot["schema_version"], 3)
            restored_again = PaperTradingEngine.restore_state(upgraded_snapshot)
            self.assertEqual(restored_again.halt_reason, "EMERGENCY_STOP")
            self.assertIn("EMERGENCY_STOP", restored_again.halt_detail)


    def test_emergency_stop_permanently_blocks_later_entries(self):
        engine = PaperTradingEngine(capital=8)
        engine.emergency_close()
        result = engine.process_scan([setup()])
        self.assertTrue(result["halted"])
        self.assertIsNone(result["open_position"])
        self.assertEqual(result["events"], [])
    def test_operator_stop_rearm_preserves_paper_history_and_cash(self):
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([setup()])
        engine.emergency_close()
        previous_cash = engine.cash
        previous_trades = [dict(item) for item in engine.closed_trades]
        previous_scans = engine.scans
        result = engine.rearm_after_operator_stop()
        self.assertEqual(result, {
            "halted": False, "rearmed": True, "live_order_submitted": False
        })
        self.assertFalse(engine.halted)
        self.assertIsNone(engine.halt_reason)
        self.assertIsNone(engine.position)
        self.assertEqual(engine.cash, previous_cash)
        self.assertEqual(engine.closed_trades, previous_trades)
        self.assertEqual(engine.scans, previous_scans)
        restored = PaperTradingEngine.restore_state(engine.export_state())
        self.assertFalse(restored.halted)
        self.assertEqual(restored.closed_trades, previous_trades)

    def test_paper_engine_rearm_rejects_max_loss_and_open_position(self):
        engine = PaperTradingEngine(capital=8)
        engine.halted = True
        engine.halt_reason = "MAX_LOSS"
        with self.assertRaisesRegex(RuntimeError, "only an explicit operator"):
            engine.rearm_after_operator_stop()
        engine.halt_reason = "EMERGENCY_STOP"
        engine.position = {"symbol": "AAA_USDT"}
        with self.assertRaisesRegex(RuntimeError, "open paper position"):
            engine.rearm_after_operator_stop()
        loss_limited = PaperTradingEngine(capital=8, max_loss_percent=1.0)
        loss_limited.halted = True
        loss_limited.halt_reason = "EMERGENCY_STOP"
        loss_limited.cash = 7.9
        with self.assertRaisesRegex(RuntimeError, "maximum cumulative paper loss"):
            loss_limited.rearm_after_operator_stop()
        self.assertTrue(loss_limited.halted)
        self.assertEqual(loss_limited.halt_reason, "MAX_LOSS")
        self.assertEqual(
            loss_limited.halt_detail, "cumulative paper loss limit remains active"
        )


    def test_engine_never_exposes_live_order_method(self):
        engine = PaperTradingEngine(capital=8)
        self.assertFalse(hasattr(engine, "create_order"))
        self.assertFalse(hasattr(engine, "new_order"))



    def test_max_cumulative_loss_closes_and_halts_engine(self):
        engine = PaperTradingEngine(
            capital=8, risk_percent=0.5, max_loss_percent=0.1,
            fee_rate=0.0005, slippage_rate=0,
        )
        engine.process_scan([setup()])
        result = engine.process_scan([
            dict(setup(price=98.0), signal="NEUTRAL")
        ])
        self.assertTrue(result["halted"])
        self.assertEqual(result["halt_reason"], "MAX_LOSS")
        self.assertEqual(
            engine.halt_detail, "cumulative paper loss limit reached during scan"
        )
        self.assertEqual(result["events"][-1]["trade"]["reason"], "STOP")
        blocked = engine.process_scan([setup("BBB_USDT")])
        self.assertTrue(blocked["halted"])
        self.assertEqual(blocked["events"], [])

    def test_state_round_trip_preserves_open_position_and_negative_closed_pnl(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        restored = PaperTradingEngine.restore_state(engine.export_state())
        self.assertEqual(restored.position, engine.position)
        self.assertAlmostEqual(restored.cash, engine.cash)

        closed = restored.process_scan([
            dict(setup(price=98.0), signal="NEUTRAL")
        ])
        self.assertEqual(closed["events"][0]["trade"]["reason"], "STOP")
        self.assertLess(closed["events"][0]["trade"]["net_pnl"], 0)
        recovered = PaperTradingEngine.restore_state(restored.export_state())
        self.assertIsNone(recovered.position)
        self.assertEqual(len(recovered.closed_trades), 1)
        self.assertLess(recovered.closed_trades[0]["net_pnl"], 0)

    def test_restore_rejects_corrupt_or_inconsistent_state(self):
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([setup()])
        valid = engine.export_state()
        invalid_states = []
        bad = dict(valid)
        bad["schema_version"] = True
        invalid_states.append(bad)
        bad = dict(valid)
        bad["cash"] = float("inf")
        invalid_states.append(bad)
        bad = dict(valid)
        bad["halted"] = True
        bad["halt_reason"] = "unexpected halt"
        invalid_states.append(bad)
        for state in invalid_states:
            with self.subTest(state=state), self.assertRaises(ValueError):
                PaperTradingEngine.restore_state(state)

    def test_max_loss_setting_rejects_boolean_and_out_of_range_values(self):
        for value in (True, False, 0, float("nan"), 25.1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PaperTradingEngine(capital=8, max_loss_percent=value)


    def test_tick_level_stop_closes_long_and_short_positions(self):
        long_engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        long_engine.process_scan([setup()])
        long_position = dict(long_engine.position)
        event = long_engine.process_tick("AAA_USDT", long_position["stop"] - 0.1)
        self.assertEqual(event["trade"]["reason"], "STOP_TICK")
        self.assertLessEqual(event["trade"]["exit"], long_position["stop"])

        short_engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        short_engine.process_scan([setup(signal="BEARISH")])
        short_position = dict(short_engine.position)
        event = short_engine.process_tick("AAA_USDT", short_position["stop"] + 0.1)
        self.assertEqual(event["trade"]["reason"], "STOP_TICK")
        self.assertGreaterEqual(event["trade"]["exit"], short_position["stop"])

    def test_tick_level_target_closes_position_without_reopening(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        position = dict(engine.position)
        event = engine.process_tick("AAA_USDT", position["target"] + 0.1)
        self.assertEqual(event["trade"]["reason"], "TARGET_TICK")
        self.assertIsNone(engine.position)
        self.assertIsNone(engine.process_tick("AAA_USDT", 100.0))

    def test_tick_reconciliation_ignores_other_symbols_and_bad_prices(self):
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([setup()])
        self.assertIsNone(engine.process_tick("BBB_USDT", 98.0))
        with self.assertRaises(ValueError):
            engine.process_tick("AAA_USDT", float("nan"))
        self.assertIsNotNone(engine.position)

if __name__ == "__main__":
    unittest.main()
