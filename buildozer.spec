[app]
title = Yemen AI
package.name=yemenai
package.domain = org.yemenai.local
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,md
version = 0.1.0
requirements = python3,kivy,arabic-reshaper,python-bidi
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 23
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True
android.debug_artifact = apk
p4a.branch = master
log_level = 2
warn_on_root = 1

[buildozer]
log_level = 2
warn_on_root = 1
