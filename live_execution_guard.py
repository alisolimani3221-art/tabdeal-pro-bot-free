"""Fail-closed policy gate for any future live futures order integration.

This module does not access credentials or the network and never submits orders.
A caller must obtain a fresh, verified trade-stream snapshot and separately
obtain explicit per-order confirmation before an order can be considered.
"""
import math
import re
import time

from risk_analysis import calculate_position_risk

MAX_RISK_PERCENT = 0.25
MAX_LEVERAGE = 1
MAX_OPEN_POSITIONS = 1
DAILY_LOSS_STOP_PERCENT = 1.0
MAX_MARKET_DATA_AGE_SECONDS = 3.0


def _finite_positive(value, name):
    if isinstance(value, bool):
        raise ValueError("{} must be positive and finite".format(name))
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("{} must be positive and finite".format(name))
    if not math.isfinite(number) or number <= 0:
        raise ValueError("{} must be positive and finite".format(name))
    return number


def evaluate_entry(*, live_enabled, explicit_confirmation, market_snapshot,
                   technical, account, capital_usdt, entry_price,
                   stop_percent, reward_risk=2.0, now=None):
    """Return a risk-capped order proposal or a fail-closed rejection.

    The function is intentionally side-effect free. It cannot place an order.
    """
    reject = lambda reason: {"allowed": False, "reason": reason}
    if live_enabled is not True:
        return reject("live_trading_disabled")
    if explicit_confirmation is not True:
        return reject("per_order_confirmation_required")
    if not isinstance(market_snapshot, dict) or not isinstance(technical, dict):
        return reject("verified_market_and_technical_data_required")
    if market_snapshot.get("verified") is not True:
        return reject("market_data_not_verified")
    if market_snapshot.get("source") != "verified_trade_stream":
        return reject("order_book_midpoint_is_not_trade_data")
    if market_snapshot.get("sequence_gap") is True or market_snapshot.get("reconnected") is True:
        return reject("market_stream_gap_or_reconnect")
    try:
        observed_at = _finite_positive(market_snapshot.get("observed_at"), "observed_at")
        current_time = time.time() if now is None else _finite_positive(now, "now")
    except ValueError:
        return reject("invalid_market_timestamp")
    age = current_time - observed_at
    if age < 0 or age > MAX_MARKET_DATA_AGE_SECONDS:
        return reject("market_data_stale")
    if technical.get("ready") is not True:
        return reject("technical_warmup_incomplete")
    signal = technical.get("signal")
    if signal not in ("BULLISH", "BEARISH"):
        return reject("no_trade_signal")
    symbol = market_snapshot.get("symbol")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
        return reject("invalid_market_symbol")
    if not isinstance(account, dict):
        return reject("account_state_unavailable")
    if (account.get("verified") is not True
            or account.get("source") != "authenticated_futures_account"):
        return reject("account_data_not_verified")
    try:
        account_age = current_time - _finite_positive(
            account.get("observed_at"), "account_observed_at"
        )
        available_balance = _finite_positive(
            account.get("available_balance"), "available_balance"
        )
    except ValueError:
        return reject("account_data_unavailable")
    if account_age < 0 or account_age > 15.0:
        return reject("account_data_stale")
    positions = account.get("positions")
    if not isinstance(positions, list):
        return reject("open_positions_unverified")
    if len(positions) >= MAX_OPEN_POSITIONS:
        return reject("maximum_open_positions_reached")
    if "daily_loss_percent" not in account:
        return reject("daily_loss_unavailable")
    try:
        daily_loss = float(account["daily_loss_percent"])
    except (TypeError, ValueError, OverflowError):
        return reject("daily_loss_unavailable")
    if not math.isfinite(daily_loss):
        return reject("invalid_daily_loss")
    if daily_loss >= DAILY_LOSS_STOP_PERCENT:
        return reject("daily_loss_limit_reached")
    try:
        capital = _finite_positive(capital_usdt, "capital_usdt")
        entry = _finite_positive(entry_price, "entry_price")
        tape_price = _finite_positive(market_snapshot.get("price"), "trade_price")
        if abs(entry - tape_price) / tape_price > 0.001:
            return reject("entry_price_differs_from_verified_trade")
        stop = _finite_positive(stop_percent, "stop_percent")
        rr = _finite_positive(reward_risk, "reward_risk")
        sizing = calculate_position_risk(
            capital, MAX_RISK_PERCENT, entry, stop, MAX_LEVERAGE, rr
        )
    except (TypeError, ValueError, OverflowError):
        return reject("invalid_or_unsafe_risk_inputs")
    if sizing["margin_estimate"] > available_balance:
        return reject("insufficient_available_margin")
    stop_price = sizing["stop_price_long"] if signal == "BULLISH" else sizing["stop_price_short"]
    target_price = sizing["target_price_long"] if signal == "BULLISH" else sizing["target_price_short"]
    return {
        "allowed": True,
        "mode": "proposal_only_no_order_sent",
        "side": "BUY" if signal == "BULLISH" else "SELL",
        "symbol": symbol,
        "quantity": sizing["quantity"],
        "notional": sizing["notional"],
        "leverage": MAX_LEVERAGE,
        "risk_percent": MAX_RISK_PERCENT,
        "risk_amount": sizing["risk_amount"],
        "stop_price": stop_price,
        "take_profit_price": target_price,
        "market_data_age_seconds": age,
        "account_data_age_seconds": account_age,
        "requires_exchange_side_sl_tp": True,
    }


def authorize_close(*, live_enabled, explicit_confirmation, symbol, positions):
    """Permit a close request only after explicit confirmation of an existing position."""
    if live_enabled is not True:
        return {"allowed": False, "reason": "live_trading_disabled"}
    if explicit_confirmation is not True:
        return {"allowed": False, "reason": "per_close_confirmation_required"}
    if not isinstance(symbol, str) or not symbol.endswith("_USDT"):
        return {"allowed": False, "reason": "invalid_symbol"}
    if not isinstance(positions, list):
        return {"allowed": False, "reason": "open_positions_unverified"}
    matches = [p for p in positions if isinstance(p, dict) and p.get("symbol") == symbol]
    if len(matches) != 1:
        return {"allowed": False, "reason": "position_not_found_or_ambiguous"}
    return {"allowed": True, "mode": "close_proposal_only_no_order_sent", "symbol": symbol}
