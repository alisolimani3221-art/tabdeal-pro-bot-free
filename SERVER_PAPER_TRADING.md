# Always-on Tabdeal paper scanner (VPS)

## Important safety boundary

This deployment is **paper trading only**. It reads public futures market data and
simulates entries/exits using `PaperTradingEngine`. It does not contain an API
key/secret, sign requests, or submit/cancel orders. Do not add exchange trading
credentials to this service. Live trading is intentionally not enabled.

## Deploy on a Linux VPS with Docker Compose

1. Clone this repository on the VPS.
2. Copy `.env.paper.example` to `.env` and adjust the *simulation* capital if needed.
3. Start it with `docker compose -f docker-compose.paper.yml up -d --build`.
4. Follow logs with `docker compose -f docker-compose.paper.yml logs -f`.
5. Stop it with `docker compose -f docker-compose.paper.yml down`.

The service scans every five minutes by default, fetches the active USDT perpetual
symbol list, analyzes closed 1-hour candles, and simulates at most one open position.
The local `paper-data/paper_state.json` ledger survives container restarts. Back up
that directory before changing or removing it.

## Configuration

- `PAPER_CAPITAL_USDT`: virtual starting capital (default 8).
- `PAPER_RISK_PERCENT`: per-trade virtual risk, capped at 0.5%.
- `PAPER_MAX_LEVERAGE`: simulated leverage cap, hard-limited to 3x.
- `PAPER_SCAN_SECONDS`: scan interval, minimum 60 seconds (default 300).
- `PAPER_WORKERS`: concurrent public-data requests, limited to 12.

## Acceptance checks before considering any next phase

- Keep the service running continuously for at least 7 days.
- Check that symbol coverage stays above the configured minimum and that failed
  requests do not create entries.
- Compare paper fills against real bid/ask spreads, fees, slippage and funding.
- Confirm that state survives a VPS/container restart and that a single position
  is not duplicated.
- Review drawdown and trade logs. Paper results do not predict live profitability.

A VPS is required for dependable 24/7 operation. GitHub Actions and an Android
app are not intended to be the always-on trading host. This service is not a
live trading bot and must not be treated as one.
