# Futures Analyzer — canonical Android source

This repository is the canonical source for the Tabdeal USDT-margined Futures Analyzer Android app.

## Baseline

- App version: **1.21**
- Android version code: **121**
- Package: `org.tabdeal.futuresanalyzer`
- Target architecture: `arm64-v8a`
- Baseline branch: `baseline/futures-analyzer-1.21`

This source baseline supersedes the previously used local APK `futuresanalyzer-1.47-arm64-v8a-release-1.apk` for ongoing source-level maintenance. The old APK is not modified or removed from any device. A source version number is not proof that an APK has been built, installed, or validated.

## Current capabilities and limits

- Reads active futures symbols and public REST order-book snapshots from Tabdeal.
- Includes a bounded, read-only scan of active USDT futures order books. Order-book snapshots are not candles and do not create technical signals.
- The experimental one-symbol paper simulator builds one-minute OHLCV candles from the observed public trade broadcast. It requires 51 closed candles plus a forming candle and resets warm-up after detected gaps or reconnects.
- The observed WebSocket payload format is empirical, not an official exchange contract. Sequence continuity, missed-event recovery, and timestamp semantics still require independent validation; see `TRADE_STREAM_SCHEMA.md`.
- Persian/Arabic font bundling and RTL support are checked during CI.
- Paper trading only: the Android runner does not submit exchange orders. The separate account adapter is read-only.

## Build and verification

The supported workflow is **Tabdeal Futures CI (ARM64 debug APK + validation)** in GitHub Actions. It compiles the Python sources, runs the regression suite, checks app/package version consistency, probes the public API, validates the trade stream, builds an ARM64 Android debug APK, checks the packaged fonts, and uploads the APK artifact when the run succeeds.

A successful workflow run is required before treating its APK artifact as installable. The artifact is a debug build, not a signed production release. No persistent release-signing key or verified signed release artifact is established. Do not assume it can update an APK signed with a different key.

## Remaining validation before trusting the simulator

1. Observe the trade stream over longer periods and multiple symbols.
2. Verify sequence ordering, duplicates, gaps, reconnect behavior, and recovery against an authoritative source.
3. Compare locally generated candles with Tabdeal's authoritative chart/feed.
4. Test Android foreground/background lifecycle and process-restart behavior.
5. Validate all simulated entry, exit, fee, slippage, stop, and target paths with regression tests.

Until these checks pass, technical simulation remains experimental. The app is foreground-only; Android may suspend it in the background.

## Security

Never commit exchange API secrets. Live order placement, stop/target placement, and live closing remain disabled. Paper results do not guarantee future performance.

## Always-on read-only monitor

A separate Docker Compose market monitor is documented in `SERVER_PAPER_TRADING.md`. It reports public order-book estimates only; it does not generate technical signals or place simulated/live orders. A Linux VPS is required for continuous operation.
