# Tabdeal Pro Bot Free

Android APK build and Tabdeal USDT-margined futures market analyzer.

## Features
- Reads active futures symbols from Tabdeal Futures API.
- Live best bid/ask and order-book display.
- Technical indicators and conservative position-risk calculations.
- Persian UI font bundling and RTL support in the Android build.
- Paper-trading simulator only; no real exchange orders are submitted.

## Build APK
1. Open the **Actions** tab.
2. Select **Build Futures Analyzer APK**.
3. Choose **Run workflow** on the `main` branch.
4. When the run succeeds, open its run page and download the `Futures-Analyzer-APK` artifact.

The workflow uses GitHub-hosted Ubuntu runners and Java 17. The APK artifact is retained for 7 days.

## Safety
The app and paper-trading engine in this repository do not submit live orders. Never put exchange API secrets in source files or commit them to this public repository. Paper results do not guarantee future performance.

## Always-on simulation
A separate Docker Compose paper-only scanner is provided in `SERVER_PAPER_TRADING.md`. It requires an independently managed Linux VPS for dependable continuous operation. GitHub Actions is used for building and testing, not as a guaranteed 24/7 trading host.
