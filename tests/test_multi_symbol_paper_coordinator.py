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

    def test_unsubscribed_symbol_is_rejected(self):
        result = self.make_coordinator().on_trade(self.trade("SOL_USDT"), received_at=10)
        self.assertEqual(result["reason"], "symbol_not_subscribed")

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

    def test_fresh_snapshots_keep_symbol_identity(self):
        coordinator = self.make_coordinator()
        for symbol in coordinator.symbols:
            coordinator.states[symbol]["builder"].is_ready = True
            coordinator.on_trade(self.trade(symbol), received_at=10)
        candidates = coordinator.candidate_snapshots(now=20)
        self.assertEqual({x["symbol"] for x in candidates}, {"BTC_USDT", "ETH_USDT"})

    def test_gap_for_other_market_does_not_halt_open_position(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        coordinator.reconcile_tick(engine, self.trade("ETH_USDT"), gap_detected=True)
        self.assertEqual(engine.gaps, [])
        self.assertEqual(engine.position, {"symbol": "BTC_USDT"})

    def test_gap_for_position_market_flattens_and_halts(self):
        coordinator = self.make_coordinator()
        engine = FakeEngine()
        result = coordinator.reconcile_tick(
            engine, self.trade("BTC_USDT"), gap_detected=True
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
        for symbols in (("BTC_USDT",), ("BTC_USDT", "BTC_USDT"),
                        ("BTC/USDT", "ETH_USDT")):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                MultiSymbolPaperCoordinator(symbols, analyze, prepare, builder_factory=FakeBuilder)


if __name__ == "__main__":
    unittest.main()
