# Futures Analyzer 1.49 — Source Baseline

- Branch: `feature/v1.49-tabdeal-paper-engine`
- Parent baseline commit: `3186f55fa7216d5b64a0e09a27b94aef381d5220`
- Baseline branch: `fix/v1.47-paper-scan-5s`
- App version: `1.49`
- Android versionCode: `149`
- Package identity retained: `org.tabdeal.futuresanalyzer`
- Existing v1.47 source and history are preserved; this branch starts from the validated v1.47 source tree.
- The 5-second paper scan, Tabdeal read-only trade broadcast integration, 1-minute candle builder, warm-up and stale-feed protections are inherited unchanged.
- Paper/simulation only. No live order placement is enabled.

## Build status

The v1.49 source branch is initialized. An APK has not yet been built. The normal workflow must pass compilation, regression tests, read-only Tabdeal checks, packaging, persistent-signature verification, and artifact verification before an APK is called complete.
