# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path, PurePosixPath


ROOT = Path(SPEC).resolve().parent
BUILD_DIR = ROOT / "build"
APP_NAME = "TaikoNautsLauncher-Portable"

HIDDEN_IMPORTS = [
    "ctypes",
    "dataclasses",
    "datetime",
    "json",
    "math",
    "msgpack",
    "msgpack._cmsgpack",
    "os",
    "pathlib",
    "shutil",
    "subprocess",
    "zipfile",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shiboken6",
]

EXCLUDED_MODULES = [
    "_hashlib",
    "_ssl",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtPdf",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtSvg",
    "PySide6.QtVirtualKeyboard",
    "ftplib",
    "http.client",
    "http.cookiejar",
    "ssl",
    "urllib.error",
    "urllib.request",
    "urllib.response",
    "webbrowser",
]

UNUSED_QT_BINARIES = {
    "opengl32sw.dll",
    "qt6network.dll",
    "qt6opengl.dll",
    "qt6pdf.dll",
    "qt6qml.dll",
    "qt6qmlmeta.dll",
    "qt6qmlmodels.dll",
    "qt6qmlworkerscript.dll",
    "qt6quick.dll",
    "qt6svg.dll",
    "qt6virtualkeyboard.dll",
    "qtnetwork.pyd",
}


def normalized_destination(entry):
    return entry[0].replace("\\", "/").lower()


def keep_binary(entry):
    destination = normalized_destination(entry)
    name = PurePosixPath(destination).name
    if destination.startswith("pyside6/plugins/"):
        return destination == "pyside6/plugins/platforms/qwindows.dll"
    if destination.startswith("pyside6/translations/"):
        return False
    return name not in UNUSED_QT_BINARIES


def keep_data(entry):
    destination = normalized_destination(entry)
    return not destination.startswith("pyside6/translations/")


a = Analysis(
    [str(BUILD_DIR / "launcher_bootstrap.pyw")],
    pathex=[str(BUILD_DIR)],
    binaries=[],
    datas=[(str(ROOT / "launcher_assets"), "launcher_assets")],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=2,
)

binary_count = len(a.binaries)
data_count = len(a.datas)
a.binaries = [entry for entry in a.binaries if keep_binary(entry)]
a.datas = [entry for entry in a.datas if keep_data(entry)]
print(f"Qt trim: binaries {binary_count} -> {len(a.binaries)}, data {data_count} -> {len(a.datas)}")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(BUILD_DIR / "TaikoNautsLauncher.ico")],
)
