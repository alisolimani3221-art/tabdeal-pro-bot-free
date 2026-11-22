[app]

title = Candle Viewer Demo
package.name = candledemo
package.domain = org.tabdeal.demo

source.dir = .
source.include_exts = py,ttf
source.include_patterns = main.py,assets/*.ttf

version = 0.1.0
requirements = python3,kivy,arabic-reshaper==3.0.0,python-bidi==0.4.2

orientation = portrait
fullscreen = 0

android.api = 35
android.minapi = 23
android.archs = arm64-v8a
android.numeric_version = 100
android.allow_backup = False
android.debug_artifact = apk

p4a.branch = v2024.01.21
android.accept_sdk_license = True

[buildozer]

log_level = 2
warn_on_root = 1
