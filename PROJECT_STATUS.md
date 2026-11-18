# Futures Analyzer — Project Status Ledger

Last updated: 2026-11-18

## Verified — do not repeat unless a relevant code change causes regression

### Android device verification (user-provided diagnostics, app v1.18)
- Android / Kivy app launches and diagnostic screen works.
- Persian font: `NotoSansArabic-Regular.ttf` loaded successfully.
- Latin font: `NotoSans-Regular.ttf` loaded successfully.
- RTL libraries and shaping: OK.
- Selected futures symbol: `BTC_USDT`.
- Order book: parsed successfully, 5 bid rows and 5 ask rows.
- Website, market ping, exchangeInfo, depth: HTTP 200.
- Depth content type: application/json.
- Depth diagnostic: HTTP 200; bids=5; asks=5; parsed=OK.
- Paper trader state at that time: STOPPED (expected safety state).

### CI verification
- Workflow run `44268515391`, commit `fd2d43453734869c400e342d15a3990206bf15e8`, completed successfully.
- The run passed Python compilation, full regression suite, version consistency, live public Tabdeal futures REST smoke test, Buildozer APK build, font/RTL packaging checks, and APK artifact upload.
- This is CI verification, not proof of private-account authentication or full device acceptance for every feature.

### API capability boundary already established
- Public futures REST endpoints tested/documented: ping, time, exchangeInfo, depth.
- Do not claim historical candle/klines or public 24h ticker availability without a fresh verified source.
- BTC_USDT device depth verification does not prove all 96 markets are currently live-tested in v1.18.

## APK update/signature findings

- Current v1.18 APK artifact was downloaded and inspected.
- Package artifact filename: `futuresanalyzer-1.18-arm64-v8a-debug.apk`.
- It is signed with Android Debug certificate SHA-256:
  `13:49:26:FB:37:57:BC:A3:6B:55:66:98:60:1A:77:2C:0C:97:2D:4E:86:30:F3:78:D5:5D:86:A9:B6:52:43:B0`.
- The private key for that debug certificate is not contained in the APK artifact and was not recovered from the repository. A new key cannot be derived from the certificate.
- Consequently, a new APK cannot be promised to update v1.18 in place. Plan for one one-time uninstall/reinstall when switching to the new permanent signing key, unless the original signing keystore is later recovered.
- After the permanent signing keystore is configured and used consistently, future APKs can be installed as in-place updates, provided package ID remains `org.tabdeal.futuresanalyzer` and versionCode increases.

## Changes made for v1.19 update path

- `main.py`: APP_VERSION changed to 1.19.
- `buildozer.spec`: version 1.19, android.numeric_version 119, android.allow_backup=True, android.release_artifact=apk. Package identity remains `org.tabdeal.futuresanalyzer`.
- `.github/workflows/android.yml`: added persistent-signing support using GitHub Actions secrets:
  - `ANDROID_KEYSTORE_BASE64`
  - `ANDROID_KEYSTORE_PASSWORD`
  - `ANDROID_KEY_ALIAS`
  - `ANDROID_KEY_PASSWORD`
- Workflow verifies APK signature and prints certificate fingerprint.
- Until all four secrets are configured, workflow falls back to a debug APK and warns that it is not a guaranteed in-place update. Do not treat such fallback artifact as the permanent update channel.
- Latest workflow run triggered by commit `04da9521d4c49cea7ca5c39e0a9d60dbe9383f96` was queued/pending at the time of this ledger update; inspect its final result before distributing an APK.

## Remaining validation — not yet marked complete

1. Configure one permanent Android release keystore in GitHub Actions secrets and verify the workflow produces a release-signed APK.
2. Confirm the new APK's package ID, versionCode, signer fingerprint, and installation behavior on the user's Galaxy A22.
3. Read-only Tabdeal account authentication from the user's device; validate wallet balance, available balance, open positions, and unrealized PnL. Never request API secret in chat.
4. Verify stable market data across all supported futures symbols; record exact count and errors from a fresh run.
5. Validate any candle/trade-stream source before enabling technical/AI analysis. Do not infer signals from midpoint alone.
6. Paper trading lifecycle: entry/exit, stop-loss, take-profit, position sizing, leverage cap, emergency stop, restart/recovery.
7. Background execution reliability on Android, including battery optimization, process death, and reconnect behavior.
8. Keep live order placement disabled until all safety tests and explicit user authorization are complete.

## Continuity rule

Use this ledger and prior verified results as the baseline. Do not repeat passed device/CI checks without a relevant code change or a specific regression report. Update this file with new evidence after each completed test. Never describe queued builds, untested features, or CI-only results as device-verified.
