# Always-on Tabdeal read-only market monitor (VPS)

## Safety and data scope

This service is **read-only**. It uses the documented public Tabdeal Futures REST endpoints:
- `/ping`
- `/exchangeInfo`
- `/depth`

The published public Futures REST documentation does not currently describe a historical candle (`klines`) or 24-hour ticker endpoint. Therefore this service does not calculate technical signals, simulate entries/exits, or submit real orders. Its midpoint is only an order-book estimate, not a last-trade price or candle close.

## Deploy on a Linux VPS with Docker Compose

1. Clone this repository on the VPS.
2. Copy `.env.paper.example` to `.env` if you want to adjust the scan interval or concurrency.
3. Start it with `docker compose -f docker-compose.paper.yml up -d --build`.
4. Follow logs with `docker compose -f docker-compose.paper.yml logs -f`.
5. Stop it with `docker compose -f docker-compose.paper.yml down`.

## Configuration

- `PAPER_SCAN_SECONDS`: scan interval, minimum 60 seconds (default 300).
- `PAPER_WORKERS`: concurrent public order-book requests, limited to 12.
- `TABDEAL_FUTURES_API`: public Futures REST base URL (defaults to the official Tabdeal endpoint).

A VPS is required for dependable continuous monitoring. GitHub Actions and an Android app are not intended to be an always-on trading host. Automatic trading remains disabled until a verified candle/trade data source and separate risk/safety tests are available.
