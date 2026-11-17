# Persian font baseline — protected

This document records the approved Persian/Arabic font behavior for the main Android application.

## Approved baseline
- The current Persian font rendering is approved by the user and must be preserved.
- Keep the bundled Noto Arabic-capable runtime font at `NotoSansArabic-Regular.ttf`.
- The Android build workflow merges the Arabic-capable Noto face with `NotoSans-Regular.ttf` so Persian, Latin letters, digits, punctuation, and market symbols render consistently.
- In `main.py`, register the bundled font as both `PersianNoto` and `Roboto`; keep all Kivy widgets on this Arabic-capable face to prevent Android/Kivy fallback squares.
- Keep Arabic reshaping and bidirectional text support bundled through `vendor/arabic_reshaper` and `vendor/bidi`.
- Keep the CI checks that verify Persian glyph coverage, Latin glyph coverage, RTL shaping, and that the fonts/libraries are packaged in the APK.

## Change policy
Do not replace, simplify, rename, or otherwise modify the approved font files, font registration, RTL shaping pipeline, or related build checks while fixing unrelated application issues. Treat this setup as the protected baseline for all future work on the main app.

Only change this baseline if the user explicitly asks for a font change or a verified Android regression requires a targeted repair. Any such change must be tested for Persian text, mixed Persian/English text, digits, ticker symbols, buttons, spinners, dialogs, and diagnostics before release.
