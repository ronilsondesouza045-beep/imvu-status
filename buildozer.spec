[app]

title = IMVU Status
package.name = imvustatus
package.domain = org.ronilson

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
source.exclude_dirs = .github, cli, .git, bin, .buildozer, __pycache__, assets/icon_src

version = 2.0.0

requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,kivymd==1.2.0,pillow,certifi

orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #0F172A

android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True
android.accept_sdk_license = True
android.debug_artifact = apk
