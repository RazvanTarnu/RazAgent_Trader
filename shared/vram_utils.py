# -*- coding: utf-8 -*-
"""Unified GPU VRAM query — pynvml (fast, ~1ms) with nvidia-smi subprocess fallback.

Replaces 4 duplicate nvidia-smi subprocess implementations across:
  - Video_Studio_Worker/pipeline/auto_pipeline.py  (_get_vram_snapshot)
  - Video_Studio_Worker/pipeline/vram_purge.py     (get_vram_mb)
  - Video_Studio_Worker/pipeline/comfyui_client.py (_get_vram_mb)
  - Video_Studio_Worker/pipeline/test_local_tts.py (get_vram_usage)

Usage:
    from vram_utils import get_vram
    used, total, free, temp = get_vram()
"""
import sys
import subprocess
import logging

logger = logging.getLogger("godclaw.vram_utils")

# ── Try pynvml at import time (one-time init) ──
_nvml_available = False
_pynvml = None
try:
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning, message=".*pynvml.*deprecated.*")
    import pynvml as _pynvml
    _pynvml.nvmlInit()
    _nvml_available = True
    logger.debug("pynvml initialized — using fast GPU queries")
except Exception:
    logger.debug("pynvml unavailable — falling back to nvidia-smi subprocess")


def get_vram(device_index: int = 0) -> tuple[int, int, int, int]:
    """Query GPU VRAM and temperature.

    Returns:
        (used_mb, total_mb, free_mb, temp_c)
        Returns (0, 0, 0, 0) on failure.
    """
    if _nvml_available:
        return _query_pynvml(device_index)
    return _query_nvidia_smi()


def _query_pynvml(device_index: int = 0) -> tuple[int, int, int, int]:
    """Fast in-process VRAM query via NVML C library (~1ms)."""
    try:
        handle = _pynvml.nvmlDeviceGetHandleByIndex(device_index)
        mem = _pynvml.nvmlDeviceGetMemoryInfo(handle)
        temp = _pynvml.nvmlDeviceGetTemperature(handle, _pynvml.NVML_TEMPERATURE_GPU)
        return (
            mem.used // (1024 * 1024),
            mem.total // (1024 * 1024),
            mem.free // (1024 * 1024),
            temp,
        )
    except Exception as exc:
        logger.debug(f"pynvml query failed: {exc} — trying nvidia-smi fallback")
        return _query_nvidia_smi()


def _query_nvidia_smi() -> tuple[int, int, int, int]:
    """Subprocess fallback via nvidia-smi CLI (~100-200ms on Windows)."""
    try:
        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,memory.free,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            startupinfo=si,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = [x.strip() for x in r.stdout.strip().split(",")]
            return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except Exception:
        pass
    return 0, 0, 0, 0


def force_vram_cleanup() -> dict:
    """Aggressive VRAM cleanup for HF mode deadlock prevention.

    Performs: GC → CUDA empty_cache → IPC collect → ComfyUI /free.
    Called between consecutive video renders in high-frequency mode.

    Returns:
        dict with {before_mb, after_mb, freed_mb, comfyui_freed}.
    """
    import gc
    used_before, total, _, _ = get_vram()

    # Step 1: Python garbage collection (2 passes for cycles)
    gc.collect()
    gc.collect()

    # Step 2: CUDA cache cleanup
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            try:
                torch.cuda.ipc_collect()
            except AttributeError:
                pass
            torch.cuda.reset_peak_memory_stats()
    except ImportError:
        pass

    # Step 3: ComfyUI model unload via API
    comfyui_freed = False
    try:
        import urllib.request
        import json as _json
        comfyui_url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
        req = urllib.request.Request(
            f"{comfyui_url}/free",
            data=_json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        comfyui_freed = True
    except Exception:
        pass

    # Step 4: Final GC + cache clear
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    used_after, _, _, _ = get_vram()
    return {
        "before_mb": used_before,
        "after_mb": used_after,
        "freed_mb": used_before - used_after,
        "comfyui_freed": comfyui_freed,
    }
