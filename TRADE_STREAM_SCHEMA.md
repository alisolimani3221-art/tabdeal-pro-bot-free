# Tabdeal public trade-broadcast schema observation

## What was observed

A read-only GitHub Actions probe connected to:

`wss://api1.tabdeal.org/special_margin/broadcast/`

It subscribed using the plain-text market symbol `BTC_USDT`. In one 25-second observation window, the client received 10 messages; 8 contained a non-empty `trade` object. The redacted report is from workflow run [44012802730](https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/44012802730). No prices, quantities, sequence values, timestamps, or account credentials were retained in that report.

The observed `trade` object had these fields and JSON types:

| Field | Observed JSON type | Observed format |
|---|---|---|
| `symbol` | string | exchange symbol such as `BTC_USDT` |
| `price` | string | positive decimal string |
| `amount` | string | positive decimal string |
| `sequence` | number | integer |
| `side_name` | string | side label; observed buy-like value |
| `updated` | string | timezone-aware ISO-8601 timestamp with six fractional digits |

The strict read-only parser now recognizes `amount` as quantity and `updated` as an ISO-8601 event timestamp. It rejects naive timestamps, timestamps more than five minutes ahead of the device clock, malformed symbols, non-positive price/amount, invalid sequence values and malformed JSON.

## What this observation does not prove

- The WebSocket documentation still does not guarantee the inner `trade` field schema.
- One short sample does not prove that messages are complete, ordered, gap-free, unique, or recoverable after disconnects.
- The meaning of `updated` and `side_name` has not been independently confirmed against an authoritative exchange specification.
- This observation does not validate historical candles, 24-hour ticker data, funding, fees, or liquidation prices.
- No candle aggregation has been enabled, and no buy/sell signal or exchange order is generated from this stream.

## Gate before enabling candle analysis

1. Capture redacted schema metadata over several hours and across multiple active symbols.
2. Verify sequence monotonicity, duplicate behavior, timestamp ordering, and gap detection.
3. Test reconnect/resubscribe behavior and determine whether missed events can be recovered.
4. Confirm the semantics of price, amount, side and timestamp against Tabdeal's official specification or exchange support.
5. Build candle aggregation only after the above checks pass; test out-of-order events, duplicate sequence IDs, missing intervals, timezone boundaries and process restarts.
6. Compare generated candles against an authoritative Tabdeal chart/feed before enabling any signal.

Until then, the Android scanner and automatic trading remain disabled. This file records an empirical observation, not an official API contract.
