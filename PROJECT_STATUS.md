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
- `.github/workflows/android.yml`: added persistent-signing support using one GitHub Actions secret: `ANDROID_SIGNING_BUNDLE_BASE64`.
- A new PKCS#12 release keystore was generated and verified. Its private signing bundle is provided separately to the user; do not commit it to the public repository.
- Workflow verifies APK signature and prints certificate fingerprint.
- Until the single secret is configured, workflow falls back to a debug APK and warns that it is not a guaranteed in-place update. Do not treat such fallback artifact as the permanent update channel.
- Latest workflow run triggered by commit `04da9521d4c49cea7ca5c39e0a9d60dbe9383f96` was queued/pending at the time of this ledger update; inspect its final result before distributing an APK.

## Remaining validation — not yet marked complete

1. Add the provided signing bundle as GitHub Actions secret `ANDROID_SIGNING_BUNDLE_BASE64`, then verify the workflow produces a release-signed APK.
2. Confirm the new APK's package ID, versionCode, signer fingerprint, and installation behavior on the user's Galaxy A22.
3. Read-only Tabdeal account authentication from the user's device; validate wallet balance, available balance, open positions, and unrealized PnL. Never request API secret in chat.
4. Verify stable market data across all supported futures symbols; record exact count and errors from a fresh run.
5. Validate any candle/trade-stream source before enabling technical/AI analysis. Do not infer signals from midpoint alone.
6. Paper trading lifecycle: entry/exit, stop-loss, take-profit, position sizing, leverage cap, emergency stop, restart/recovery.
7. Background execution reliability on Android, including battery optimization, process death, and reconnect behavior.
8. Keep live order placement disabled until all safety tests and explicit user authorization are complete.


## Latest verified build — 2026-11-18, workflow run #291

- Run: https://github.com/alisolimani3221-art/tabdeal-pro-bot-free/actions/runs/44284132524
- Commit: 8130115dc6f6546fd3497b690594ae1a25d9f53b; result: completed / success.
- The persistent signing secret was present in the run environment (masked by GitHub); the signing bundle was unpacked and used. The debug fallback build was skipped.
- APK signature verification passed. Release certificate SHA-256 fingerprint: `9a:41:c3:22:95:b7:72:3e:e8:df:ad:d4:c4:0e:e7:db:dc:a9:fb:8a:6c:62:e4:3a:ab:c0:9c:9b:ef:74:1a:e5`.
- Artifact: `Futures-Analyzer-APK`, artifact ID `14419620411`, ZIP SHA-256 `cd850ec4119fcbf6fe200491bb8043e0c4fc48363531f5aa10c0243bbe894cb5`, size 20,889,166 bytes.
- Full regression suite, live public Tabdeal REST smoke test, RTL/font packaging checks and APK upload all passed.
- The old v1.18 APK was previously verified with a different Android debug certificate. Therefore, if v1.18 is still installed, v1.19 may require one uninstall/reinstall to switch to this permanent release signer. Once v1.19 is installed, future builds using this same key and a higher versionCode should update in place.
- Still not verified: the actual v1.19 APK installation on the user's phone, preservation of settings/API configuration, private account read-only authentication on-device, and live data stability across all symbols.

## Continuity rule

Use this ledger and prior verified results as the baseline. Do not repeat passed device/CI checks without a relevant code change or a specific regression report. Update this file with new evidence after each completed test. Never describe queued builds, untested features, or CI-only results as device-verified.


## Latest verification — 2026-11-20, v1.27

- User-provided Android v1.27 diagnostic at 2026-11-20 07:23:48 local: Persian and Latin fonts loaded; RTL libraries/shaping OK; BTC_USDT order book parsed; 5 bids and 5 asks; website, market ping, market info, and depth all HTTP 200; paper trader reported RUNNING.
- GitHub Actions run #551 (run ID 44669166862), commit 449801435c10cb7653a985a56d0231b20005671c, completed successfully on 2026-11-20. Regression suite, Python compilation, version consistency, public API smoke test, live read-only order-book scan for every active futures market, read-only trade-stream probe, Persian font/RTL packaging, signed release APK build, signature verification, and APK artifact upload all passed.
- Release APK artifact: Futures-Analyzer-APK, artifact ID 14599591664, extracted file `futuresanalyzer-1.27-arm64-v8a-release.apk`, size 21,209,580 bytes; SHA-256 `fd26e76bfd0511b8cacb0b2d3c9631ca2a61a4eebbca0dbb282f5a93a6814e5f`.
- CI success and the user's device diagnostic are separate evidence. Still not device-verified: secure API credential persistence, private account read-only authentication/balance/positions, long-duration background/reconnect behavior, and full paper-trading lifecycle on the phone. Keep live order placement disabled until safety validation and explicit authorization.
- Continuity: use the active repository `alisolimani3221-art/tabdeal-pro-bot-free`; the older `tabdeal-pro-bot` repository contains a retired Android workflow. Do not repeat the old failed builds or already-passed v1.27 checks without a relevant code change/regression.
