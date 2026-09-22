"""CUDA DLL discovery for CTranslate2 on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PREPARED = False

# Subdirectories of the `nvidia` namespace package that ship Windows DLLs
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
        # Some CTranslate2 builds resolve via PATH rather than the DLL directory list
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


_GPU_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def gpu_names() -> list[str]:
    """Display adapter names, read from the registry so it costs nothing."""
    if sys.platform != "win32":
        return []
    import winreg

    names: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _GPU_KEY) as root:
            for i in range(16):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        desc, _ = winreg.QueryValueEx(key, "DriverDesc")
                except OSError:
                    continue
                if isinstance(desc, str) and desc not in names:
                    names.append(desc)
    except OSError:
        pass
    return names


def gpu_vendor() -> str:
    """One of nvidia, amd, intel or unknown, based on the adapter names."""
    joined = " ".join(gpu_names()).lower()
    if "nvidia" in joined or "geforce" in joined or "quadro" in joined:
        return "nvidia"
    if "amd" in joined or "radeon" in joined:
        return "amd"
    if "intel" in joined:
        return "intel"
    return "unknown"


def physical_cores() -> int:
    """Physical core count, falling back to half the logical count."""
    try:
        import os as _os

        count = _os.cpu_count() or 4
    except Exception:
        count = 4
    return max(2, count // 2)


def resolve_backend(device: str, compute_type: str, model: str,
                    cpu_threads: int) -> tuple[str, str, str, int, str]:
    """Turn "auto" settings into concrete ones for this machine.

    CTranslate2 only accelerates on CUDA, so an AMD or Intel GPU means the CPU does
    the work. A GPU-sized model then runs slower than real time, which is why the
    model is chosen alongside the device rather than fixed.
    """
    reason = ""
    want_auto_device = device in ("", "auto")

    if want_auto_device:
        ok, detail = cuda_available()
        device = "cuda" if ok else "cpu"
        vendor = gpu_vendor()
        if ok:
            reason = f"CUDA available ({detail})"
        elif vendor in ("amd", "intel"):
            reason = (f"{vendor.upper()} GPU found, which CTranslate2 cannot use; "
                      "running on the CPU")
        else:
            reason = f"no CUDA device ({detail}); running on the CPU"

    if compute_type in ("", "auto"):
        compute_type = "float16" if device == "cuda" else "int8"

    if model in ("", "auto"):
        # large-v3-turbo is 2.3x slower than real time on a six-core CPU, small is
        # about 0.6x, which keeps up with a conversation
        model = "large-v3-turbo" if device == "cuda" else "small"

    if cpu_threads <= 0:
        cpu_threads = min(8, physical_cores()) if device == "cpu" else 0

    return device, compute_type, model, cpu_threads, reason
