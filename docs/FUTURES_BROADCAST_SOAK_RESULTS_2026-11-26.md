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


## Follow-up validation and corrected thirty-minute soak

A review found that the original soak wrapper could report success after an early WebSocket close if it had already received at least one valid trade. The wrapper was hardened: it now requires at least 99% of the requested observation duration, a connected socket, valid trade events, consistent message counts, and no recorded WebSocket error. Two regression tests cover early termination and disconnect/error cases.

- Corrected regression run: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/45853219249 — success; **529 tests passed**.
- Corrected independent 30-second broadcast probe: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/45853219226 — success; 30.2 seconds, 45 messages, 37 fresh valid trades, 37 with sequence, 0 without, diagnostic `ok`.
- The earlier 30-minute run (45850858106) was cancelled when the corrected branch revision superseded it; it is **not** counted as a successful soak.
- Corrected 30-minute soak: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/45853208250 — started on the corrected revision and must complete the full observation window before it can pass.

The duration guard reduces false-positive success from early closure; it still does not prove lossless delivery or official sequence semantics. The corrected 30-minute observation remains a single-symbol, read-only stability test.
