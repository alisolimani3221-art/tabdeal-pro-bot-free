# Futures Analyzer v1.57 release verification

Date: 2026-11-28 (Asia/Tehran)
Repository: alisolimani3221-art/tabdeal-pro-bot-free
Source branch: fix/android-foreground-market-service-v156-20261127
Source commit built: 3e14ddfe1e30a594e4a8defceba7d256b9d6d96b
Pull request: #93 (open; not merged at time of verification)
Workflow run: 46101691858 (run #1647), conclusion: success

## Build evidence
- Full Python compilation: passed.
- Full unittest regression suite: passed.
- App/package version consistency: passed.
- Live read-only Tabdeal API smoke test: passed.
- Read-only market, trade-stream and broadcast probes: completed successfully.
- Signed release APK build: passed.
- Persistent APK signing certificate verification: passed.
- APK output and packaged Persian fonts verification: passed.
- Installable APK artifact: Futures-Analyzer-APK (artifact ID 15246163881).
- APK inside artifact: futuresanalyzer-1.57-arm64-v8a-release.apk
- APK size: 25,606,195 bytes.
- APK SHA-256: 0e470a4b5f168dfb61e6ace658c1a2c7fa2552667520534c71844347e9fa5ced

## Scope and limitations
- This build is paper-only; no exchange orders are submitted.
- CI success verifies compilation, tests, packaging and signing, not physical-device acceptance.
- The 2026-11-28 device diagnostic before installing this build showed order book OK, WebSocket connected, 88 durable candles and 11 continuous candles; the paper engine had halted on DATA_GAP.
- The same diagnostic showed the Android background keepalive service had not started due to an Android Build.VERSION AttributeError. PR #93 adds a same-process Android foreground service and regression coverage; this exact APK was built successfully from that PR head.
- Still required: install this APK on the user's Android device and verify background/resume behavior, candle continuity/recovery, and paper-engine warm-up. Do not enable live trading until these device checks pass.
