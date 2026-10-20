[app]

title = IMVU Status
package.name = imvustatus
package.domain = org.ronilson

source.dir = .
source.include_exts = py,png,jpg,atlas,json
source.exclude_dirs = .github, .git, bin, .buildozer, .gradle, .pytest_cache, __pycache__, .venv, venv, ci, assets/icon_src, server, web, tests, cli, dados

version = 3.0.0

requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,kivymd==1.2.0,pillow,certifi,requests,urllib3,charset-normalizer,idna,plyer

orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #070B12

android.permissions = INTERNET,ACCESS_NETWORK_STATE,POST_NOTIFICATIONS
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = False
android.accept_sdk_license = True
android.debug_artifact = apk
android.release_artifact = apk
