"""Read-only accessors for persisted paper-trade history (never live account data)."""
import json
import math
import os

from paper_trader import PaperTradingEngine


def summarize_paper_trades(trades):
    """Validate and summarize closed paper trades without mutating engine state."""
    if not isinstance(trades, (list, tuple)):
        raise ValueError("closed paper trades must be a list")
    rows = []
    for item in trades:
        if not isinstance(item, dict):
            raise ValueError("closed paper trade is malformed")
        symbol = item.get("symbol")
        side = item.get("side")
        if not isinstance(symbol, str) or not symbol or side not in ("BULLISH", "BEARISH"):
            raise ValueError("closed paper trade identity is invalid")
        pnl = item.get("net_pnl")
        if isinstance(pnl, bool) or not isinstance(pnl, (int, float)) or not math.isfinite(float(pnl)):
            raise ValueError("closed paper trade P/L is invalid")
        rows.append(dict(item))
    wins = sum(1 for row in rows if float(row["net_pnl"]) > 0)
    losses = sum(1 for row in rows if float(row["net_pnl"]) < 0)
    return {
        "trades": rows,
        "count": len(rows),
        "wins": wins,
        "losses": losses,
        "net_pnl": sum(float(row["net_pnl"]) for row in rows),
    }


def load_paper_history(state_path, engine=None):
    """Read only closed paper trades from the existing validated paper state."""
    if engine is not None:
        lock = getattr(engine, "_lock", None)
        if lock is None:
            raise ValueError("paper engine lock is unavailable")
        with lock:
            return summarize_paper_trades(list(engine.closed_trades))
    if not os.path.isfile(state_path):
        return summarize_paper_trades([])
    with open(state_path, "r", encoding="utf-8") as stream:
        state = json.load(stream)
    restored = PaperTradingEngine.restore_state(state)
    return summarize_paper_trades(restored.closed_trades)
