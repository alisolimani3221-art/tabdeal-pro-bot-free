# Tabdeal Pro Bot Free

Android APK build and Tabdeal USDT-margined futures market analyzer.

## Features
- Reads active futures symbols from Tabdeal Futures API.
- Current REST snapshot of best bid/ask and order book (not a continuous trade stream).
- Conservative position-risk calculations (technical signals are disabled until a verified Tabdeal candle feed is available).
- Persian UI font bundling and RTL support in the Android build.
- A conservative paper-trading engine is included as a separately tested module. The Android automatic scanner is deliberately disabled until a verified candle/trade-data feed is available; no real exchange orders are submitted.

## Build APK
1. Open the **Actions** tab.
2. Select **Build Futures Analyzer APK**.
3. Choose **Run workflow** on the `main` branch.
4. When the run succeeds, open its run page and download the `Futures-Analyzer-APK` artifact.

The workflow uses GitHub-hosted Ubuntu runners and Java 17. The APK artifact is retained for 7 days.

## Safety
The app and paper-trading engine in this repository do not submit live orders. Never put exchange API secrets in source files or commit them to this public repository. Paper results do not guarantee future performance.

## Always-on read-only market monitor
A separate Docker Compose read-only Tabdeal order-book monitor is provided in `SERVER_PAPER_TRADING.md`. It checks documented public endpoints and reports bid/ask/midpoint estimates only. It does not create signals or simulated/live orders. A Linux VPS is required for continuous operation.
