# PyInstaller spec for AI Klipers.
#
# MUST be run on Windows to produce a Windows .exe -- PyInstaller does not
# cross-compile. From the project root, on a Windows machine with
# requirements-dev.txt installed:
#
#     pyinstaller build_exe.spec
#
# Output lands in dist/AI Klipers/AI Klipers.exe (onedir mode -- see note
# below on why this is the default instead of onefile).

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)

# faster-whisper / ctranslate2 and opencv both dynamically discover
# plugins/data files that PyInstaller's static analysis can't always see,
# so collect their datas/binaries explicitly.
hidden_imports = [
    "ctranslate2",
    "faster_whisper",
    "cv2",
    "yt_dlp",
    "sqlalchemy.sql.default_comparator",
]

datas = [
    (str(project_root / "ui"), "ui"),
    (str(project_root / "icons"), "icons"),
    (str(project_root / "assets"), "assets"),
]
# Bundle a static ffmpeg/ffprobe if you've placed one in ./ffmpeg (see
# README.md) -- editor/ffmpeg_utils.py looks there first before PATH.
ffmpeg_dir = project_root / "ffmpeg"
if any(ffmpeg_dir.glob("*.exe")):
    datas.append((str(ffmpeg_dir), "ffmpeg"))

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI Klipers",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # windowed app, no console popup
    icon=str(project_root / "icons" / "app.ico") if (project_root / "icons" / "app.ico").exists() else None,
)

# onedir (COLLECT) rather than onefile: a single .exe that unpacks
# PySide6 + OpenCV + ctranslate2 into a temp dir on every launch adds
# several seconds of startup lag and complicates AV/SmartScreen behaviour.
# onedir starts instantly and is what Inno Setup (installer/installer.iss)
# expects to package. If you specifically need one portable .exe file,
# change exclude_binaries=True to False above, drop the COLLECT() block,
# and pass the binaries/datas straight into EXE() instead.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="AI Klipers",
)
