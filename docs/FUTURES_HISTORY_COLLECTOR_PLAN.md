# Safe Tabdeal Futures History and Continuous Collector Plan

Status: design only; not connected to the trading engine.

## Decision

Do not call an undocumented REST path, use Spot candles, or import another exchange's candles as Tabdeal Futures history. The currently tested public Futures paths `/r/fapi/v1/klines`, `/r/fapi/v1/trades`, and `/r/fapi/v1/aggTrades` returned HTTP 404 for both `BTC_USDT` and `BTCUSDT`. The public Futures SDK currently exposes ping, time, exchangeInfo, depth and aggDepth, but no public historical-candle or recent-public-trades method.

The documented Spot `trades` method is not evidence of a Futures endpoint and is not an acceptable substitute.

## Candidate architecture

A separate always-on collector may eventually reduce Android restart warm-up, but it is not yet approved for signal generation.

1. Run as a supervised service on an always-on host, not inside the Android foreground process.
2. Start with one Futures market and one independent read-only broadcast connection. Do not multiplex symbols or increase connection count until Tabdeal confirms the protocol and connection limits.
3. Persist each validated normalized event to an append-only journal before acknowledging it to the candle builder. Use SQLite transactions and durable commits; retain raw payloads only if credentials and personal data are excluded and retention is explicitly bounded.
4. Record connection epochs, receive time, exchange event time, symbol, sequence (when present), price and quantity. Do not assume sequence IDs are contiguous or globally/per-symbol monotonic until Tabdeal confirms their semantics.
5. Treat every disconnect, process restart, malformed event, out-of-order event, unexplained sequence discontinuity, or timestamp discontinuity as a data-integrity incident. Keep previous records for diagnosis, but mark the affected history ineligible until continuity is re-established.
6. Build OHLCV only from verified Futures events and the exchange's confirmed candle boundary/timezone/empty-minute semantics. Do not invent zero-volume bars or forward-fill prices until the official chart definition is known.
7. Expose a read-only authenticated API to the Android app. The app may import a history snapshot only when it includes symbol, interval, source, time range, connection epoch, validation state, and a cryptographic content digest. The app independently validates timestamps, OHLCV, duplicates, gaps, and closed-bar status.
8. Keep the multi-timeframe entry gate fail-closed. Any unknown provenance, gap, incomplete timeframe, stale snapshot, or schema mismatch means no entry.
9. Keep the collector and Android app paper-only. The collector has no exchange API keys and no order/account endpoints.

## Empirical broadcast observation (2026-11-26)

A new read-only 30-second CI probe connected to
`wss://api1.tabdeal.org/special_margin/broadcast/`, sent the observed plain-text
`BTC_USDT` subscription, and received fresh events accepted by the conservative
trade parser:

- Push run: 25 messages, 23 fresh valid trades; elapsed 30.16 seconds.
- Pull-request run: 22 messages, 21 fresh valid trades; elapsed 30.20 seconds.
- Sequence-coverage run: 51 messages, 36 fresh valid trades; all 36 valid
  trades included a sequence field; zero valid trades omitted it; elapsed
  30.17 seconds.

The sequence-coverage result is one short observation only. The Android parser
and candle builder continue to treat sequence as optional, reject repeated or
older sequence values when comparable within the current connection, and do not
assume consecutive IDs or preserve sequence continuity across reconnects.
A sequence jump alone is not treated as proof of lost trades because Tabdeal
has not documented the sequence semantics.

This confirms that the endpoint currently delivers parseable fresh BTC Futures
trade events using the observed envelope and fields. It does **not** prove
lossless delivery, sequence-ID semantics, replay/backfill support, or official
candle-boundary and no-trade-minute rules. The probe logs counts and parser
diagnostics only; it does not log prices or quantities.

The Android foreground consumer already uses this broadcast stream. The
separate always-on history collector described above remains design-only and
is not deployed or authorized as a historical-data source.

## Required confirmation from Tabdeal

Before production implementation, obtain written answers to:

- Is there a supported public Futures historical kline endpoint? If yes: exact base URL, path, parameters, symbol spelling, intervals, maximum limit, pagination, retention, rate limits, and candle timestamp/close semantics.
- If no kline endpoint, is there a supported public Futures recent-trades endpoint with historical pagination/backfill?
- What exactly do broadcast `sequence` and `updated` mean? Are sequence IDs monotonic per symbol, contiguous, unique across reconnects, and replayed after reconnect?
- Does the broadcast guarantee delivery of every trade? How are reconnects, duplicates, out-of-order events, and backfill handled?
- Are there connection/subscription limits, and is one symbol per connection mandatory?
- How are no-trade minutes represented in official Futures candles?

## Acceptance gates

A collector cannot feed trading analysis until all gates pass:

- Official endpoint/protocol confirmation is recorded.
- A multi-hour soak test demonstrates stable connections and bounded memory/disk use.
- Reconnect, process kill, network loss, duplicate, out-of-order and timestamp-gap tests all fail closed. Missing-sequence behavior must follow the official protocol contract; until Tabdeal confirms whether sequence is mandatory, the current live parser records coverage but does not invent that requirement.
- Each supported symbol's generated OHLCV is compared against Tabdeal's official Futures chart for matching time windows, including low-volume periods and empty minutes.
- A restart restores history only when the journal proves continuity; otherwise readiness is reset.
- The Android importer rejects tampered, stale, wrong-symbol, wrong-interval, incomplete, and non-contiguous snapshots.
- The 1m/5m/15m gates remain closed until at least 23 verified closed candles per timeframe are present and directions agree.
- Full regression, Android build, signed APK verification, and physical-device acceptance pass.

## Current safe behavior

Until the above is satisfied, the Android app uses its existing foreground live stream only. A restart or confirmed gap may require a fresh warm-up. This is slower, but it avoids fabricating a market history or generating a signal from data that cannot be proven complete.

No collector service is deployed by this design document, no unverified endpoint is added, and no live trading is enabled.
