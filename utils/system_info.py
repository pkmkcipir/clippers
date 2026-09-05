"""System, GPU and hardware-acceleration detection helpers.

All detection here is defensive: every check is wrapped so that a
missing driver / library / binary degrades to "not available" instead
of raising and crashing the app. This module intentionally avoids a
hard dependency on torch -- it only needs to answer "is there a usable
NVIDIA GPU", not run anything on it.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache

from utils.logger import get_logger

log = get_logger("system_info")


@dataclass
class GpuInfo:
    available: bool
    name: str | None = None
    vendor: str | None = None  # "nvidia" | "intel" | "amd" | None


@lru_cache(maxsize=1)
def detect_nvidia_gpu() -> GpuInfo:
    """Detect an NVIDIA GPU via nvidia-smi (no torch/CUDA toolkit required)."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return GpuInfo(available=False)
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            name = result.stdout.strip().splitlines()[0]
            return GpuInfo(available=True, name=name, vendor="nvidia")
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("nvidia-smi probe failed: %s", exc)
    return GpuInfo(available=False)


@lru_cache(maxsize=1)
def detect_whisper_device() -> str:
    """Return 'cuda' if faster-whisper/ctranslate2 can use a GPU, else 'cpu'."""
    if not detect_nvidia_gpu().available:
        return "cpu"
    try:
        import ctranslate2  # noqa: F401
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception as exc:  # pragma: no cover - defensive, lib may be absent
        log.debug("ctranslate2 CUDA probe failed, falling back to CPU: %s", exc)
    return "cpu"


@lru_cache(maxsize=1)
def detect_ffmpeg_hw_encoders() -> list[str]:
    """Return the hardware H.264/HEVC encoders ffmpeg was built with AND
    that appear usable on this machine (NVENC / QuickSync / AMF)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return []

    built_in = result.stdout
    candidates = {
        "nvidia": "h264_nvenc",
        "quicksync": "h264_qsv",
        "amf": "h264_amf",
    }
    available = [vendor for vendor, enc in candidates.items() if enc in built_in]

    # Compiled-in support doesn't guarantee the hardware/driver is present.
    # We only *confirm* NVIDIA here since nvidia-smi is a cheap, reliable
    # signal; QuickSync/AMF are left as "maybe" for the caller to try and
    # gracefully fall back from.
    if "nvidia" in available and not detect_nvidia_gpu().available:
        available.remove("nvidia")

    return available


def recommend_hw_accel() -> str:
    encoders = detect_ffmpeg_hw_encoders()
    if "nvidia" in encoders:
        return "nvidia"
    if "quicksync" in encoders:
        return "quicksync"
    if "amf" in encoders:
        return "amf"
    return "cpu"


def get_resource_usage() -> dict:
    """CPU/RAM usage for the Settings page. Uses psutil if installed,
    otherwise returns None values rather than crashing the UI."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "ram_percent": vm.percent,
            "ram_used_gb": round(vm.used / (1024 ** 3), 1),
            "ram_total_gb": round(vm.total / (1024 ** 3), 1),
        }
    except ImportError:
        log.warning("psutil not installed; resource usage unavailable")
        return {"cpu_percent": None, "ram_percent": None, "ram_used_gb": None, "ram_total_gb": None}
