import unittest

from multi_symbol_paper_coordinator import MultiSymbolPaperCoordinator


class FakeBuilder:
    def __init__(self, symbol):
        self.symbol = symbol
        self.is_ready = False
        self.closed = [(60_000, 1, 1, 1, 1, 1)]
        self.received = []
        self.resets = 0

    def add_trade(self, trade):
        if trade.get("symbol") != self.symbol:
            return {"accepted": False, "reason": "symbol_mismatch"}
        self.received.append(dict(trade))
        return {"accepted": True, "gap_detected": bool(trade.get("gap_detected"))}

    def candles_for_analysis(self):
        return [[60_000, 1, 1, 1, 1, 1]]

    def reset_after_reconnect(self):
        self.resets += 1

    def status(self):
        return {"symbol": self.symbol}


class FakeEngine:
    def __init__(self):
        self.position = {"symbol": "BTC_USDT"}
        self.ticks = []
        self.gaps = []
        self.scans = []

    def process_tick(self, symbol, price):
        if self.position is not None and self.position["symbol"] == symbol:
            self.ticks.append((symbol, price))
        return None

    def halt_for_data_gap(self, symbol, price):
        if self.position is None or self.position["symbol"] != symbol:
            return None
        self.gaps.append((symbol, price))
        self.position = None
        return {"halted": True}

    def process_scan(self, candidates):
        self.scans.append(list(candidates))
        return {"candidate_count": len(candidates)}


def analyze(_candles):
    return {"signal": "NEUTRAL", "price": 10}


def prepare(snapshot, price):
    result = dict(snapshot)
    result["price"] = price
    return result


class MultiSymbolPaperCoordinatorTests(unittest.TestCase):
    def make_coordinator(self):
        return MultiSymbolPaperCoordinator(
            ("BTC_USDT", "ETH_USDT"), analyze, prepare,
            builder_factory=FakeBuilder, max_snapshot_age=30,
        )

    @staticmethod
    def trade(symbol, price=10, **extra):
        return {"ok": True, "symbol": symbol, "price": price,
                "quantity": 1, "timestamp_ms": 60_000, **extra}

    def test_routes_each_trade_to_its_own_builder(self):
        coordinator = self.make_coordinator()
        coordinator.states["BTC_USDT"]["builder"].is_ready = True
        coordinator.states["ETH_USDT"]["builder"].is_ready = True
        coordinator.on_trade(self.trade("BTC_USDT"), received_at=10)
        coordinator.on_trade(self.trade("ETH_USDT", 20), received_at=11)
        btc = coordinator.states["BTC_USDT"]["builder"].received
        eth = coordinator.states["ETH_USDT"]["builder"].received
        self.assertEqual([x["symbol"] for x in btc], ["BTC_USDT"])
        self.assertEqual([x["symbol"] for x in eth], ["ETH_USDT"])
        self.assertEqual(coordinator.states["BTC_USDT"]["latest_technical"]["price"], 10)
        self.assertEqual(coordinator.states["ETH_USDT"]["latest_technical"]["price"], 20)

    def test_contextual_analyzer_receives_exchange_event_timestamp(self):
        received = []
        def contextual(candles, now_ms):
            received.append(now_ms)
            return {"signal": "NEUTRAL", "price": 10}
        coordinator = MultiSymbolPaperCoordinator(
            ("BTC_USDT",), analyze, prepare, builder_factory=FakeBuilder,
            analyze_with_context=contextual,
        )
        coordinator.states["BTC_USDT"]["builder"].is_ready = True
        coordinator.on_trade(self.trade("BTC_USDT", timestamp_ms=180_000),
                             received_at=10)
        self.assertEqual(received, [180_000])

    def test_contextual_analysis_is_cached_until_a_new_closed_bar(self):
        calls = []
        def contextual(candles, now_ms):
            calls.append(now_ms)
            return {"signal": "NEUTRAL", "price": 10}
        coordinator = MultiSymbolPaperCoordinator(
            ("BTC_USDT",), analyze, prepare, builder_factory=FakeBuilder,
            analyze_with_context=contextual,
        )
        coordinator.states["BTC_USDT"]["builder"].is_ready = True
        coordinator.on_trade(self.trade("BTC_USDT", timestamp_ms=180_000),
                             received_at=10)
        coordinator.on_trade(self.trade("BTC_USDT", price=11, timestamp_ms=181_000),
                             received_at=11)
        self.assertEqual(calls, [180_000])
        self.assertEqual(coordinator.candidate_snapshots(now=12)[0]["price"], 11)

    def test_invalid_price_does_not_mutate_candle_builder(self):
        coordinator = self.make_coordinator()
        result = coordinator.on_trade(
            self.trade("BTC_USDT", float("nan")), received_at=10
        )
        state = coordinator.states["BTC_USDT"]
        self.assertEqual(result["reason"], "invalid_price")
        self.assertEqual(state["builder"].received, [])
        self.assertEqual(state["accepted_trades"], 0)
        self.assertEqual(state["rejected_trades"], 1)

    def test_unsubscribed_symbol_is_rejected(self):
        result = self.make_coordinator().on_trade(self.trade("SOL_USDT"), received_at=10)
        self.assertEqual(result["reason"], "symbol_not_subscribed")

    def test_five_symbol_builders_and_snapshots_are_isolated(self):
        symbols = ("BTC_USDT", "ETH_USDT", "XRP_USDT", "BNB_USDT", "SOL_USDT")
        coordinator = MultiSymbolPaperCoordinator(
            symbols, analyze, prepare, builder_factory=FakeBuilder,
            max_snapshot_age=30,
        )
        for index, symbol in enumerate(symbols):
            coordinator.states[symbol]["builder"].is_ready = True
            result = coordinator.on_trade(
                self.trade(symbol, price=100 + index), received_at=10 + index
            )
            self.assertTrue(result["accepted"])
            self.assertEqual(
                coordinator.states[symbol]["builder"].received[0]["symbol"],
                symbol,
            )
        candidates = coordinator.candidate_snapshots(now=20)
        self.assertEqual({item["symbol"] for item in candidates}, set(symbols))
        self.assertEqual(
            {item["price"] for item in candidates},
            {100, 101, 102, 103, 104},
        )

    def test_single_symbol_coordinator_is_supported_for_safe_fallback(self):
        coordinator = MultiSymbolPaperCoordinator(
            ("BTC_USDT",), analyze, prepare,
            builder_factory=FakeBuilder, max_snapshot_age=30,
        )
        self.assertEqual(coordinator.symbols, ("BTC_USDT",))

    def test_unready_symbol_cannot_create_candidate(self):
        coordinator = self.make_coordinator()
        coordinator.on_trade(self.trade("BTC_USDT"), received_at=10)
        self.assertEqual(coordinator.candidate_snapshots(now=11), [])

    def test_stale_snapshot_is_not_passed_to_engine(self):
        coordinator = self.make_coordinator()
        coordinator.states["BTC_USDT"]["builder"].is_ready = True
        coordinator.on_trade(self.trade("BTC_USDT"), received_at=10)
        engine = FakeEngine()
        result = coordinator.process_scan(engine, now=41)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(engine.scans, [[]])

    def test_process_scan_receives_all_fresh_symbol_candidates(self):
        coordinator = self.make_coordinator()
        for symbol in coordinator.symbols:
            coordinator.states[symbol]["builder"].is_ready = True
            coordinator.on_trade(self.trade(symbol), received_at=10)
        engine = FakeEngine()
        result = coordinator.process_scan(engine, now=20)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(
            {item["symbol"] for item in engine.scans[-1]},
            {"BTC_USDT", "ETH_USDT"},
        )

    def test_fresh_snapshots_keep_symbol_identity(self):
        coordinator = self.make_coordinator()
        for symbol in coordinator.symbols:
            coordinator.states[symbol]["builder"].is_ready = True
            coordinator.on_trade(self.trade(symbol), received_at=10)
        candidates = coordinator.candidate_snapshots(now=20)
        self.assertEqual({x["symbol"] for x in candidates}, {"BTC_USDT", "ETH_USDT"})

    def test_normal_tick_for_other_market_does_not_mark_open_position(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        event = self.trade("ETH_USDT")
        coordinator.on_trade(event, received_at=10)
        coordinator.reconcile_tick(engine, event, gap_detected=False)
        self.assertEqual(engine.ticks, [])
        self.assertEqual(engine.position, {"symbol": "BTC_USDT"})

    def test_tick_for_secondary_position_is_reconciled_by_shared_engine(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        engine.position = {"symbol": "ETH_USDT"}
        event = self.trade("ETH_USDT", 20)
        coordinator.on_trade(event, received_at=10)
        coordinator.reconcile_tick(engine, event, gap_detected=False)
        self.assertEqual(engine.ticks, [("ETH_USDT", 20)])
        self.assertEqual(engine.position, {"symbol": "ETH_USDT"})

    def test_lowercase_tick_symbol_is_normalized(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        event = self.trade("btc_usdt")
        coordinator.on_trade(event, received_at=10)
        coordinator.reconcile_tick(engine, event, gap_detected=False)
        self.assertEqual(engine.ticks, [("BTC_USDT", 10)])

    def test_status_does_not_mark_future_timestamp_as_fresh(self):
        coordinator = self.make_coordinator()
        coordinator.states["BTC_USDT"]["builder"].is_ready = True
        coordinator.on_trade(self.trade("BTC_USDT"), received_at=20)
        self.assertFalse(coordinator.status(now=10)["BTC_USDT"]["snapshot_fresh"])

    def test_status_exposes_timeframe_warmup_and_entry_readiness(self):
        coordinator = self.make_coordinator()
        state = coordinator.states["BTC_USDT"]
        state["last_trade_monotonic"] = 10
        state["latest_technical"] = {
            "timeframe_analysis": {
                "ready": False, "agreement": False,
                "closed_candles": {"5": 200, "15": 66, "30": 33, "60": 12},
            }
        }
        status = coordinator.status(now=11)["BTC_USDT"]
        self.assertFalse(status["multi_timeframe_ready"])
        self.assertFalse(status["multi_timeframe_agreement"])
        self.assertFalse(status["entry_ready"])
        self.assertEqual(status["timeframe_closed_candles"]["60"], 12)

    def test_reconcile_tick_rejects_malformed_events_before_engine_mutation(self):
        coordinator = self.make_coordinator()
        invalid_events = (
            self.trade("BTC_USDT", float("nan")),
            self.trade("BTC_USDT", float("inf")),
            self.trade("BTC_USDT", True),
            self.trade("BTC_USDT", 10, quantity=0),
            self.trade("BTC_USDT", 10, timestamp_ms=0),
            self.trade("BTC_USDT", 10, timestamp_ms=True),
            self.trade("BTC_USDT", 10, ok=False),
        )
        for event in invalid_events:
            with self.subTest(event=event):
                engine = FakeEngine()
                self.assertIsNone(
                    coordinator.reconcile_tick(engine, event, gap_detected=False)
                )
                self.assertEqual(engine.ticks, [])
                self.assertEqual(engine.gaps, [])
                self.assertEqual(engine.position, {"symbol": "BTC_USDT"})

    def test_reconcile_tick_rejects_non_boolean_gap_flag(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        event = self.trade("BTC_USDT")
        coordinator.on_trade(event, received_at=10)
        self.assertIsNone(
            coordinator.reconcile_tick(
                engine, event, gap_detected="yes"
            )
        )
        self.assertEqual(engine.ticks, [])
        self.assertEqual(engine.gaps, [])
        self.assertEqual(engine.position, {"symbol": "BTC_USDT"})

    def test_gap_for_other_market_does_not_halt_open_position(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        event = self.trade("ETH_USDT")
        coordinator.on_trade(event, received_at=10)
        coordinator.reconcile_tick(engine, event, gap_detected=True)
        self.assertEqual(engine.gaps, [])
        self.assertEqual(engine.position, {"symbol": "BTC_USDT"})

    def test_gap_for_position_market_flattens_and_halts(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        event = self.trade("BTC_USDT")
        coordinator.on_trade(event, received_at=10)
        result = coordinator.reconcile_tick(
            engine, event, gap_detected=True
        )
        self.assertTrue(result["halted"])
        self.assertEqual(engine.gaps, [("BTC_USDT", 10)])
        self.assertIsNone(engine.position)

    def test_reconnect_invalidates_every_symbol_snapshot(self):
        coordinator = self.make_coordinator()
        for symbol in coordinator.symbols:
            state = coordinator.states[symbol]
            state["builder"].is_ready = True
            coordinator.on_trade(self.trade(symbol), received_at=10)
        coordinator.reset_after_reconnect()
        self.assertEqual(coordinator.candidate_snapshots(now=11), [])
        self.assertEqual(
            [coordinator.states[s]["builder"].resets for s in coordinator.symbols],
            [1, 1],
        )

    def test_duplicate_symbols_and_invalid_universe_are_rejected(self):
        for symbols in ((), ("BTC_USDT", "BTC_USDT"),
                        ("BTC/USDT", "ETH_USDT")):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                MultiSymbolPaperCoordinator(symbols, analyze, prepare, builder_factory=FakeBuilder)

    def test_tick_requires_matching_accepted_event_and_is_single_use(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        event = self.trade("BTC_USDT", 12)
        self.assertIsNone(coordinator.reconcile_tick(engine, event))
        self.assertEqual(engine.ticks, [])
        coordinator.on_trade(event, received_at=10)
        altered = dict(event, price=13)
        self.assertIsNone(coordinator.reconcile_tick(engine, altered))
        self.assertEqual(engine.ticks, [])
        self.assertIsNone(coordinator.reconcile_tick(engine, event))
        self.assertEqual(engine.ticks, [("BTC_USDT", 12)])
        self.assertIsNone(coordinator.reconcile_tick(engine, event))
        self.assertEqual(engine.ticks, [("BTC_USDT", 12)])

    def test_reconnect_clears_pending_tick(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        event = self.trade("BTC_USDT", 12)
        coordinator.on_trade(event, received_at=10)
        coordinator.reset_after_reconnect()
        self.assertIsNone(coordinator.reconcile_tick(engine, event))
        self.assertEqual(engine.ticks, [])

    def test_invalid_quantity_timestamp_and_sequence_do_not_mutate_builder(self):
        invalid = (
            self.trade("BTC_USDT", 10, quantity=float("nan")),
            self.trade("BTC_USDT", 10, quantity=0),
            self.trade("BTC_USDT", 10, timestamp_ms=True),
            self.trade("BTC_USDT", 10, timestamp_ms=0),
            self.trade("BTC_USDT", 10, sequence=True),
            self.trade("BTC_USDT", 10, sequence=-1),
        )
        for event in invalid:
            with self.subTest(event=event):
                coordinator = self.make_coordinator()
                result = coordinator.on_trade(event, received_at=10)
                state = coordinator.states["BTC_USDT"]
                self.assertFalse(result["accepted"])
                self.assertEqual(result["reason"], "invalid_trade_fields")
                self.assertEqual(state["builder"].received, [])
                self.assertEqual(state["accepted_trades"], 0)
                self.assertIsNone(state["pending_tick"])


if __name__ == "__main__":
    unittest.main()
