# Tabdeal Futures broadcast soak results — 2026-11-26

Branch: `fix/multitimeframe-risk-exit-20261126`
Commit observed: `9ea70673725882ce3d41f0c4092779d9729816ec`

## Five-minute read-only observation

GitHub Actions run: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/45847960831

- Connection established: yes
- Observation duration: 300.21 seconds
- Total WebSocket messages: 387
- Fresh valid Futures trade events accepted by parser: 286
- Events with sequence field: 286
- Events without sequence field: 0
- Sequence-field coverage in this sample: 100%
- Last parser diagnostic: `ok`
- Workflow result: success

The probe subscribed to the observed `BTC_USDT` broadcast stream and emitted aggregate metadata only; it did not print trade prices or quantities.

## Independent 30-second PR observation

GitHub Actions run: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/45847971283

- Connection established: yes
- Observation duration: 30.0 seconds
- Total messages: 43
- Fresh valid Futures trade events: 32
- Events with sequence field: 32
- Events without sequence field: 0
- Parser diagnostic: `ok`
- Workflow result: success

## Regression checks

- Push regression run: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/45847960869 — success.
- Pull-request regression run: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/45847971554 — success.
- Both jobs completed compilation and the complete unittest suite.

## Interpretation and limits

This is stronger evidence that the currently observed broadcast connection remained usable for one five-minute window and that the conservative parser accepted 286 fresh BTC Futures events. All accepted events in this sample carried a sequence field.

It does **not** prove lossless delivery, complete market coverage, official sequence semantics, sequence continuity across reconnects, replay/backfill, candle boundary rules, or no-trade-minute behavior. A five-minute observation is not the required multi-hour soak test. Sequence remains optional in the parser until Tabdeal documents its contract; sequence jumps are not treated as proof of missing trades.

No historical collector is deployed, no Spot data is substituted, and no live trading is enabled. The collector remains design-only and the app must continue to fail closed when candle history/continuity cannot be proven.
