# Tabdeal Pro Bot Free

Android APK build and Tabdeal USDT-margined futures market analyzer.

## Current release status (1.19)
- Android app version and Buildozer package version are 1.19 (versionCode 119).
- The repository defines an experimental **debug** APK workflow that supports manual runs and automatic builds after relevant app/build changes on `main`. A persistent release-signing keystore and a verified signed release artifact have not been established; do not treat the experimental APK as a production release or assume it can update an APK signed with a different key.
- The app reads active futures symbols and a current REST order-book snapshot from Tabdeal.
- Order-book parsing rejects empty, malformed, crossed, non-finite and unverified-market responses.
- Persian UI font bundling and RTL support are verified during the Android build.
- The Android app includes a bounded, read-only scan of all active USDT futures order books (four concurrent requests on-device), showing best bid/ask and spread. These snapshots are not candles and do not generate technical signals.
- The paper simulator remains limited to the currently selected symbol. It builds one-minute OHLCV candles from the empirically observed public trade stream, requires 51 closed candles plus a forming candle before analysis, and resets warm-up after a data gap. It is foreground-only and paper-only.
- The WebSocket trade payload schema is an empirical observation only; it is not treated as an official contract. See `TRADE_STREAM_SCHEMA.md`.

## Features
- Reads active futures symbols from Tabdeal Futures API.
- Current REST snapshot of best bid/ask and order book (not a continuous trade stream).
- Conservative position-risk calculations (technical signals remain gated on verified candle continuity).
- Persian UI font bundling and RTL support in the Android build.
- A read-only WebSocket probe observed a redacted Tabdeal trade-event schema; this is empirical, not an official inner-message contract. See `TRADE_STREAM_SCHEMA.md`.
- A conservative paper-trading engine uses closed-candle indicators, a 0.5% maximum risk budget per simulated trade, at most 3x simulated leverage, stop-loss and take-profit. It never submits exchange orders.
- The all-market scanner ranks order-book snapshots by spread only; it does not pretend that order-book data is a technical trading signal.
- `tabdeal_trade.py` contains a separate futures adapter for authenticated read-only account/position/order queries. All mutating order, SL/TP, and close paths remain blocked until exchange-side behavior and protection can be independently verified; the Android automatic runner is paper-only.

## Build APK
The standard `Tabdeal Futures CI (APK build paused)` workflow is validation-only: it runs compilation, regression tests, API checks, and read-only market probes. It does **not** build or upload an APK.

A separate workflow named **Experimental APK build (v1.19)** is available under **Actions**. It can be run manually from the `main` branch, and it also runs automatically when app source files, Buildozer settings, requirements, bundled fonts/assets, or the workflow itself change on `main`.

For a manual build:
1. Open the **Actions** tab.
2. Select **Experimental APK build (v1.19)**.
3. Choose **Run workflow** on the `main` branch.
4. Wait for the build to finish successfully, then download `Futures-Analyzer-v1.19-experimental-APK` from that run's Artifacts section.

This is an experimental debug APK, not a verified release build. It uses GitHub-hosted Ubuntu runners, Java 17, ARM64, and bundles the Persian/Arabic and Latin Noto fonts. Artifacts are retained for 7 days. A successful validation-only CI run does not mean an APK was produced.

## Safety
The current Android app and paper-trading engine do not submit live orders. The trade adapter exposes authenticated read-only queries; every mutating order, SL/TP, and close path remains blocked and is not wired into the app. Do not enable automatic live trading until exchange-side order behavior, symbol precision/minimums, position reconciliation, protective stop/target placement, restart recovery, and emergency-close behavior are tested against the actual account. Never put exchange API secrets in source files or commit them to this public repository. Paper results do not guarantee future performance.

The on-device stream only runs while the app remains active; Android may suspend it in the background, so it is not an always-on cloud service.

## Always-on read-only market monitor
A separate Docker Compose read-only Tabdeal order-book monitor is provided in `SERVER_PAPER_TRADING.md`. It checks documented public endpoints and reports bid/ask/midpoint estimates only. It does not create signals or simulated/live orders. A Linux VPS is required for continuous operation.
