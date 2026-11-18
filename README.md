# Tabdeal Pro Bot Free

Android APK build and Tabdeal USDT-margined futures market analyzer.

## Current release status (1.19)
- Android app version and Buildozer package version are 1.19 (versionCode 119).
- The release APK is signed with the configured persistent release keystore. After the one-time switch from the older debug-signed 1.18 build, subsequent releases must keep this signing key and increase versionCode to support in-place updates.
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
- `tabdeal_trade.py` contains a separate futures trade adapter based on the official Tabdeal Python SDK endpoint definitions. It is disabled by default and requires both explicit client enablement and per-operation confirmation. It is not yet connected to the Android UI or automatic strategy.

## Build APK
1. Open the **Actions** tab.
2. Select **Build Futures Analyzer APK**.
3. Choose **Run workflow** on the `main` branch.
4. When the run succeeds, open its run page and download the `Futures-Analyzer-APK` artifact.

The workflow uses GitHub-hosted Ubuntu runners and Java 17. The APK artifact is retained for 7 days.

## Safety
The current Android app and paper-trading engine do not submit live orders. The new trade adapter is a separately tested integration layer, but it is disabled by default and not wired into the app. Do not enable automatic live trading until exchange-side order behavior, symbol precision/minimums, position reconciliation, protective stop/target placement, restart recovery, and emergency-close behavior are tested against the actual account. Never put exchange API secrets in source files or commit them to this public repository. Paper results do not guarantee future performance.

The on-device stream only runs while the app remains active; Android may suspend it in the background, so it is not an always-on cloud service.

## Always-on read-only market monitor
A separate Docker Compose read-only Tabdeal order-book monitor is provided in `SERVER_PAPER_TRADING.md`. It checks documented public endpoints and reports bid/ask/midpoint estimates only. It does not create signals or simulated/live orders. A Linux VPS is required for continuous operation.
