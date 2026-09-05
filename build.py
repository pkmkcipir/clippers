"""Convenience build script -- run this ON WINDOWS to produce the .exe.

    python build.py

Equivalent to `pyinstaller build_exe.spec`, but checks a few things first
so a broken build fails fast with a clear message instead of a wall of
PyInstaller traceback.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"[build.py] FAILED: {message}")
        sys.exit(1)


def main() -> None:
    print("[build.py] AI Klipers build checklist")

    check(platform.system() == "Windows",
          "PyInstaller does not cross-compile -- this must run on a Windows "
          "machine (or VM) to produce a working .exe.")

    check(shutil.which("pyinstaller") is not None,
          "pyinstaller not found. Run: pip install -r requirements-dev.txt")

    ffmpeg_dir = ROOT / "ffmpeg"
    has_bundled_ffmpeg = any(ffmpeg_dir.glob("*.exe"))
    if not has_bundled_ffmpeg and shutil.which("ffmpeg") is None:
        print("[build.py] WARNING: no ffmpeg.exe in ./ffmpeg and none on PATH.\n"
              "           The built app will fail at runtime the first time it needs to "
              "cut/encode/transcribe. Download a static build (e.g. from "
              "https://www.gyan.dev/ffmpeg/builds/) and place ffmpeg.exe + "
              "ffprobe.exe in ./ffmpeg before building, or install ffmpeg system-wide.")

    print("[build.py] Running PyInstaller (this can take several minutes)...")
    result = subprocess.run([sys.executable, "-m", "PyInstaller", "build_exe.spec", "--noconfirm"],
                             cwd=ROOT)
    check(result.returncode == 0, "PyInstaller build failed -- see output above.")

    dist_dir = ROOT / "dist" / "AI Klipers"
    print(f"[build.py] Done. App folder: {dist_dir}")
    print("[build.py] Next: build installer/installer.iss with Inno Setup, "
          "or zip the dist folder for a portable version.")


if __name__ == "__main__":
    main()
