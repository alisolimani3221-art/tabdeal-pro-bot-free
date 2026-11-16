"""Long-running, read-only Tabdeal futures paper trader for a VPS.

This process NEVER signs requests or submits exchange orders. It only reads
public market data and persists a local paper-trading ledger.
"""
import json
import logging
import os
import signal
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from market_symbols import list_market_symbols
from paper_trader import PaperTradingEngine
from risk_analysis import analyze_klines

API = os.getenv("TABDEAL_FUTURES_API", "https://api1.tabdeal.org/r/fapi/v1").rstrip("/")
STATE_PATH = Path(os.getenv("PAPER_STATE_PATH", "/data/paper_state.json"))
CAPITAL = float(os.getenv("PAPER_CAPITAL_USDT", "8"))
RISK = float(os.getenv("PAPER_RISK_PERCENT", "0.5"))
LEVERAGE = int(os.getenv("PAPER_MAX_LEVERAGE", "3"))
INTERVAL = max(60, int(os.getenv("PAPER_SCAN_SECONDS", "300")))
PAPER_ONCE = os.getenv("PAPER_ONCE", "0").lower() in ("1", "true", "yes")
WORKERS = max(1, min(12, int(os.getenv("PAPER_WORKERS", "8"))))
STOP = False

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tabdeal-paper")


def request_json(session, path, params=None, timeout=20):
    response = session.get(API + path, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def restore_engine():
    engine = PaperTradingEngine(capital=CAPITAL, risk_percent=RISK, max_leverage=LEVERAGE)
    if not STATE_PATH.exists():
        return engine
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if float(state["initial_capital"]) != CAPITAL:
            raise ValueError("configured paper capital differs from saved ledger")
        engine.initial_capital = float(state["initial_capital"])
        engine.cash = float(state["cash"])
        engine.position = state.get("position")
        engine.closed_trades = state.get("closed_trades", [])
        engine.scans = int(state.get("scans", 0))
        log.info("Restored paper ledger: scans=%s cash=%.4f", engine.scans, engine.cash)
    except Exception:
        log.exception("Saved paper ledger is invalid; refusing to overwrite it")
        raise
    return engine


def persist_engine(engine):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mode": "PAPER_ONLY",
        "initial_capital": engine.initial_capital,
        "cash": engine.cash,
        "position": engine.position,
        "closed_trades": engine.closed_trades,
        "scans": engine.scans,
        "saved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    fd, temporary = tempfile.mkstemp(prefix="paper-state-", dir=str(STATE_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def analyze_symbol(symbol):
    # The exchange-native form may contain an underscore; the API also accepts
    # the compact form on some deployments. Try both without guessing a ticker.
    last_error = None
    for api_symbol in (symbol.replace("_", ""), symbol):
        try:
            with requests.Session() as session:
                session.headers.update({"User-Agent": "Tabdeal-Paper-Scanner/1.0"})
                candles = request_json(
                    session, "/klines",
                    {"symbol": api_symbol, "interval": "1h", "limit": 100},
                    timeout=15,
                )
            if isinstance(candles, dict):
                candles = candles.get("data", candles.get("result", candles.get("klines")))
            result = analyze_klines(candles)
            result["symbol"] = symbol
            return result
        except Exception as exc:
            last_error = exc
    raise RuntimeError("{}: {}".format(symbol, last_error))


def scan(engine):
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Tabdeal-Paper-Scanner/1.0", "Accept": "application/json"})
        info = request_json(session, "/exchangeInfo", timeout=25)
    if isinstance(info, dict):
        symbols_data = info.get("symbols")
        if symbols_data is None:
            nested = info.get("data", {})
            symbols_data = nested.get("symbols") if isinstance(nested, dict) else nested
    else:
        symbols_data = info
    symbols = list_market_symbols(symbols_data)
    if not symbols:
        raise RuntimeError("No active USDT perpetual contracts returned by exchangeInfo")

    results, failures = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(analyze_symbol, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append((futures[future], str(exc)[:180]))

    minimum = max(10, len(symbols) // 2)
    if len(results) < minimum:
        raise RuntimeError(
            "Insufficient market data: {} of {} symbols (need at least {})".format(
                len(results), len(symbols), minimum
            )
        )

    result = engine.process_scan(results)
    persist_engine(engine)
    position = result.get("open_position")
    log.info(
        "PAPER scan=%d symbols=%d analyzed=%d errors=%d setups=%d equity=%.4f "
        "cash=%.4f closed=%d net_closed_pnl=%+.4f position=%s",
        engine.scans, len(symbols), len(results), len(failures),
        len(result["candidates"]), result["equity"], result["cash"],
        len(engine.closed_trades),
        sum(t["net_pnl"] for t in engine.closed_trades),
        json.dumps(position, ensure_ascii=False, sort_keys=True) if position else "NONE",
    )
    for event in result["events"]:
        log.info("PAPER_EVENT %s", json.dumps(event, ensure_ascii=False, sort_keys=True))
    if failures:
        log.warning("Sample market-data errors: %s", failures[:5])


def stop_handler(_signum, _frame):
    global STOP
    STOP = True
    log.warning("Shutdown requested; no live exchange orders exist.")


def main():
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    engine = restore_engine()
    log.warning(
        "PAPER-ONLY service started. capital=%.2f risk=%.2f%% leverage_cap=%dx "
        "scan=%ds; live trading is disabled.",
        CAPITAL, RISK, LEVERAGE, INTERVAL,
    )
    while not STOP:
        started = time.monotonic()
        try:
            scan(engine)
        except Exception:
            log.exception("Paper scan failed; ledger remains unchanged for this scan.")
        if PAPER_ONCE:
            break
        remaining = max(1, INTERVAL - int(time.monotonic() - started))
        while remaining and not STOP:
            time.sleep(min(1, remaining))
            remaining -= 1
    persist_engine(engine)
    log.info("Paper-only service stopped cleanly.")


if __name__ == "__main__":
    main()
