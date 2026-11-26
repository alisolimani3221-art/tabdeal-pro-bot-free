# Tabdeal public trade-broadcast schema observation

## What was observed

A read-only GitHub Actions probe connected to:

`wss://api1.tabdeal.org/special_margin/broadcast/`

It subscribed using the plain-text market symbol `BTC_USDT`. In a later 15-second observation window, the client received 24 messages and 20 matched the strict parser; the redacted report is retained in the latest successful APK workflow. This remains a short empirical sample. No prices, quantities, sequence values, timestamps, or account credentials were retained in that report.

The observed `trade` object had these fields and JSON types:

| Field | Observed JSON type | Observed format |
|---|---|---|
| `symbol` | string | exchange symbol such as `BTC_USDT` |
| `price` | string | positive decimal string |
| `amount` | string | positive decimal string |
| `sequence` | number | integer |
| `side_name` | string | side label; observed buy-like value |
| `updated` | string | timezone-aware ISO-8601 timestamp with six fractional digits |

The strict read-only parser now recognizes `amount` as quantity and `updated` as an ISO-8601 event timestamp. It rejects naive timestamps, timestamps more than five minutes ahead of the device clock, malformed symbols, non-positive price/amount, missing or invalid sequence values and malformed JSON. Sequence is mandatory in the runtime path: without it, a repeated delivery cannot be distinguished safely from two legitimate trades sharing the same timestamp, price and amount.

## What this observation does not prove

- The WebSocket documentation still does not guarantee the inner `trade` field schema.
- One short sample does not prove that messages are complete, ordered, gap-free, unique, or recoverable after disconnects.
- The meaning of `updated` and `side_name` has not been independently confirmed against an authoritative exchange specification.
- This observation does not validate historical candles, 24-hour ticker data, funding, fees, or liquidation prices.
- The Android app now aggregates normalized events into one-minute OHLCV candles for the currently selected symbol, but this is an experimental, local paper-simulation path—not a validated production trading feed.
- The first candle after subscription and every reconnect is discarded; timestamp gaps (data gaps) clear the warm-up history and force a fresh warm-up; indicators never bridge a data gap. Analysis waits for at least 51 closed candles plus a forming candle.
- Paper simulation uses closed-candle indicators and conservative local risk limits. No exchange order is generated or submitted.

## Remaining validation before wider deployment

1. Capture redacted schema metadata over several hours and across multiple active symbols.
2. Verify sequence monotonicity, duplicate behavior, timestamp ordering, and gap detection.
3. Test reconnect/resubscribe behavior and determine whether missed events can be recovered.
4. Confirm the semantics of price, amount, side and timestamp against Tabdeal's official specification or exchange support.
5. Continue testing out-of-order events, duplicate sequence IDs, missing intervals, timezone boundaries and process restarts.
6. Compare generated candles against an authoritative Tabdeal chart/feed before relying on signals.
7. Extend from the selected symbol to a multi-symbol service only after connection limits, rate limits and sustained data quality are known.

The Android app's current simulator is one-symbol and foreground-only. It is not guaranteed to continue while Android suspends or closes the app. Live exchange orders remain disabled. This file records empirical observations, not an official API contract.
