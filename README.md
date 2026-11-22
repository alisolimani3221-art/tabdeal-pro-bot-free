# Tabdeal Pro Bot Free

Android APK build and Tabdeal USDT-margined futures market analyzer.

## Current development status (1.47)
- Current development branch: `feature/v1.47-groq-free-ai`; app version and Buildozer package version are 1.47 (versionCode 147). This branch is not yet a released or device-verified APK.
- The v1.26 development branch defines a persistent release-signing workflow. A signed APK is not considered released until the build, certificate fingerprint, packaged fonts, and uploaded artifact are all verified.
- The app reads active futures symbols and a current REST order-book snapshot from Tabdeal.
- Order-book parsing rejects empty, malformed, crossed, non-finite and unverified-market responses.
- Persian UI font bundling and RTL support are verified during the Android build.
- The Android app includes a bounded, read-only scan of all active USDT futures order books (four concurrent requests on-device), showing best bid/ask and spread. These snapshots are not candles and do not generate technical signals.
- The paper simulator remains limited to the currently selected symbol. It builds one-minute OHLCV candles from the empirically observed public trade stream, requires 51 closed candles plus a forming candle before analysis, and resets warm-up after a data gap. It is foreground-only and paper-only.
- The WebSocket trade payload schema is an empirical observation only; it is not treated as an official contract. See `TRADE_STREAM_SCHEMA.md`.

## Version 1.47 — Groq conversational assistant
- The in-app multi-turn assistant uses the Groq OpenAI-compatible chat completions API and the `qwen/qwen3.8-27b` model.
- The API key is entered for the current app session only and is not persisted by the app.
- Only a bounded, allow-listed, read-only diagnostics and public-market snapshot is sent; account credentials, balances, positions, and order controls are excluded.
- The assistant cannot place or manage exchange orders.

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
The `.github/workflows/android.yml` workflow runs compilation, regression tests, Tabdeal read-only API checks, a scan of active futures order books, a read-only trade-stream probe, Android packaging, persistent-keystore signing, certificate verification, APK content checks, and artifact upload. A successful run is required before treating an APK as installable. The workflow expects the repository secret `ANDROID_SIGNING_BUNDLE_BASE64` to contain the established release keystore bundle. Artifacts are retained for 14 days.

## Safety
The current Android app and paper-trading engine do not submit live orders. The trade adapter exposes authenticated read-only queries; every mutating order, SL/TP, and close path remains blocked and is not wired into the app. Do not enable automatic live trading until exchange-side order behavior, symbol precision/minimums, position reconciliation, protective stop/target placement, restart recovery, and emergency-close behavior are tested against the actual account. Never put exchange API secrets in source files or commit them to this public repository. Paper results do not guarantee future performance.

The on-device stream only runs while the app remains active; Android may suspend it in the background, so it is not an always-on cloud service.

## Always-on read-only market monitor
A separate Docker Compose read-only Tabdeal order-book monitor is provided in `SERVER_PAPER_TRADING.md`. It checks documented public endpoints and reports bid/ask/midpoint estimates only. It does not create signals or simulated/live orders. A Linux VPS is required for continuous operation.


## Version 1.32 – Android Keystore varargs fix

Pass AES-GCM block mode and padding as scalar PyJNIus varargs (`setBlockModes("GCM")` and `setEncryptionPaddings("NoPadding")`) instead of wrapping them in Python lists. This avoids the nested-sequence conversion path that can raise `Could not construct a Java String[]`. Credentials remain encrypted with Android Keystore AES-256-GCM; there is no plaintext fallback. Device-level validation is still required.


## Version 1.33 – read-only account API connection hardening

The account reader now uses a dedicated verified TLS session on Android, synchronizes the signed-request timestamp against Tabdeal's public futures server-time endpoint, and includes a 10-second receive window. Account access remains GET-only; no order placement or withdrawal endpoints are used. The CI tests validate signing and server-time parsing; a real Android device test is still required.
