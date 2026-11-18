[app]

title = Futures Analyzer
package.name = futuresanalyzer
package.domain = org.tabdeal

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,ttf,txt
source.include_patterns = assets/*.ttf,assets/*.txt

version = 1.20
requirements = python3,kivy,requests,websocket-client==1.8.0,certifi,arabic-reshaper==3.0.0,python-bidi==0.4.2,future==1.0.0

orientation = portrait
fullscreen = 0

android.permissions = INTERNET

android.api = 35
android.minapi = 23
android.archs = arm64-v8a
android.numeric_version = 120
android.allow_backup = True
android.release_artifact = apk

p4a.branch = v2024.01.21
android.accept_sdk_license = True

[buildozer]

log_level = 2
warn_on_root = 1
