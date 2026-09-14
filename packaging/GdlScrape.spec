# -*- mode: python ; coding: utf-8 -*-

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent
incompatible_icu = re.compile(r"^icu(?:uc|in|dt).*\.dll$", re.IGNORECASE)
gallery_dl_hiddenimports = collect_submodules("gallery_dl.extractor")

a = Analysis(
    [str(project_root / "gdlscrape.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / "gallery_dl_app" / "assets"), "gallery_dl_app/assets")],
    hiddenimports=gallery_dl_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Poppler installations on PATH can make PyInstaller collect incompatible ICU
# DLLs at the application root. Qt uses the Windows system ICU API instead.
a.binaries = [
    entry
    for entry in a.binaries
    if not (Path(entry[0]).parent == Path(".") and incompatible_icu.match(Path(entry[0]).name))
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GdlScrape",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_root / "packaging" / "version_info.txt"),
    icon=[str(project_root / "packaging" / "gdlscrape.ico")],
)
