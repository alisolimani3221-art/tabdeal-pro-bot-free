[app]

title = Futures Analyzer
package.name = futuresanalyzer
package.domain = org.tabdeal

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,ttf,txt
source.include_patterns = assets/*.ttf,assets/*.txt

version = 1.57
requirements = python3,kivy,requests,websocket-client==1.8.0,certifi,arabic-reshaper==3.0.0,python-bidi==0.4.2,future==1.0.0

orientation = portrait
fullscreen = 0

android.permissions = INTERNET, FOREGROUND_SERVICE, FOREGROUND_SERVICE_SPECIAL_USE, POST_NOTIFICATIONS, WAKE_LOCK

android.api = 35
android.minapi = 24
android.archs = arm64-v8a
android.numeric_version = 157
android.allow_backup = False
android.release_artifact = apk

p4a.branch = develop
p4a.commit = 32994bea648eddf6808477ac91cd74632287a7fe
android.add_src = android_src
p4a.extra_args = --extra-manifest-application-xml "<service android:name=\"org.tabdeal.futuresanalyzer.MarketKeepService\" android:exported=\"false\" android:foregroundServiceType=\"specialUse\"><property android:name=\"android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE\" android:value=\"Continuous user-requested read-only futures market monitoring while the app is backgrounded\" /></service>"
android.accept_sdk_license = True

[buildozer]

log_level = 2
warn_on_root = 1
