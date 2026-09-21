"""CUDA DLL discovery for CTranslate2 on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PREPARED = False

# Subdirectories of the `nvidia` namespace package that ship Windows DLLs.
_NVIDIA_DLL_SUBDIRS = ("cublas", "cudnn", "cuda_runtime", "cufft")


def prepare_hf_cache() -> None:
    """Stop huggingface_hub from symlinking inside its cache."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _nvidia_dll_dirs() -> list[Path]:
    """Locate bin/ directories inside installed nvidia-*-cu12 wheels."""
    dirs: list[Path] = []
    for site_dir in {Path(p) for p in sys.path if p}:
        nvidia_root = site_dir / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for sub in _NVIDIA_DLL_SUBDIRS:
            bin_dir = nvidia_root / sub / "bin"
            if bin_dir.is_dir() and any(bin_dir.glob("*.dll")):
                dirs.append(bin_dir)
    return dirs


def prepare_cuda() -> list[str]:
    """Add wheel-provided CUDA DLL directories to the loader search path."""
    global _PREPARED
    if _PREPARED or sys.platform != "win32":
        return []

    registered: list[str] = []
    for bin_dir in _nvidia_dll_dirs():
        try:
            os.add_dll_directory(str(bin_dir))
        except (OSError, AttributeError):
            continue
        registered.append(str(bin_dir))
        # Some CTranslate2 builds resolve via PATH rather than the DLL directory list.
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")

    _PREPARED = True
    return registered


def cuda_available() -> tuple[bool, str]:
    """Best-effort check that CTranslate2 can actually see a CUDA device."""
    prepare_cuda()
    try:
        import ctranslate2
    except Exception as exc:  # pragma: no cover - import environment dependent
        return False, f"ctranslate2 import failed: {exc}"

    try:
        count = ctranslate2.get_cuda_device_count()
    except Exception as exc:
        return False, f"CUDA probe failed: {exc}"

    if count < 1:
        return False, "no CUDA device visible to CTranslate2"
    return True, f"{count} CUDA device(s)"
