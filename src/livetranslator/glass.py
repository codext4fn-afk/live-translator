"""Windows compositor effects: real backdrop blur behind the overlay."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

# DwmSetWindowAttribute
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38

_CORNER_DEFAULT = 0
_CORNER_DONOTROUND = 1
_CORNER_ROUND = 2
_BACKDROP_NONE = 1  # DWMSBT_NONE
_BACKDROP_ACRYLIC = 3  # DWMSBT_TRANSIENTWINDOW

# SetWindowCompositionAttribute
_WCA_ACCENT_POLICY = 19
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_ACCENT_ENABLE_BLURBEHIND = 3


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(_AccentPolicy)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _tint_argb(r: int, g: int, b: int, a: int) -> int:
    """Pack a tint for the accent policy, which expects AABBGGRR."""
    return (a << 24) | (b << 16) | (g << 8) | r


def _apply_dwm(hwnd: int, settings: tuple[tuple[int, int], ...]) -> bool:
    try:
        dwm = ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return False

    ok = False
    for attribute, value in settings:
        try:
            result = dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_int(attribute),
                ctypes.byref(ctypes.c_int(value)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            continue
        if result == 0 and attribute == _DWMWA_SYSTEMBACKDROP_TYPE:
            ok = True
    return ok


def _apply_accent(hwnd: int, *, tint: tuple[int, int, int, int]) -> bool:
    try:
        user32 = ctypes.windll.user32
        set_attribute = user32.SetWindowCompositionAttribute
    except (AttributeError, OSError):
        return False

    policy = _AccentPolicy()
    policy.AccentState = _ACCENT_ENABLE_ACRYLICBLURBEHIND
    policy.AccentFlags = 0x20 | 0x40 | 0x80 | 0x100  # draw all borders
    policy.GradientColor = _tint_argb(*tint)
    policy.AnimationId = 0

    data = _WindowCompositionAttributeData()
    data.Attribute = _WCA_ACCENT_POLICY
    data.Data = ctypes.pointer(policy)
    data.SizeOfData = ctypes.sizeof(policy)

    try:
        if set_attribute(wintypes.HWND(hwnd), ctypes.byref(data)):
            return True
        # Some builds refuse acrylic on a tool window but allow plain blur.
        policy.AccentState = _ACCENT_ENABLE_BLURBEHIND
        return bool(set_attribute(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def round_window(hwnd: int, width: int, height: int, radius: int) -> bool:
    """Clip the window to a rounded rectangle."""
    if sys.platform != "win32" or width <= 0 or height <= 0:
        return False
    try:
        gdi = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        # CreateRoundRectRgn takes ellipse *diameters*, not radii.
        region = gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1,
                                        radius * 2, radius * 2)
        if not region:
            return False
        # The window owns the region after this call; do not delete it.
        return bool(user32.SetWindowRgn(wintypes.HWND(hwnd), region, True))
    except Exception:
        log.debug("could not set window region", exc_info=True)
        return False


def apply(hwnd: int, *, acrylic: bool = False,
          tint: tuple[int, int, int, int] = (24, 24, 30, 150)) -> str:
    """Give the window a blurred backdrop. """
    if sys.platform != "win32":
        return "none"

    if acrylic and _apply_accent(hwnd, tint=tint):
        _apply_dwm(hwnd, (
            (_DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (_DWMWA_WINDOW_CORNER_PREFERENCE, _CORNER_ROUND),
            (_DWMWA_SYSTEMBACKDROP_TYPE, _BACKDROP_ACRYLIC),
        ))
        return "acrylic"

    # Painted mode: DWM must not draw anything at all.
    _apply_dwm(hwnd, (
        (_DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
        (_DWMWA_WINDOW_CORNER_PREFERENCE, _CORNER_DONOTROUND),
        (_DWMWA_SYSTEMBACKDROP_TYPE, _BACKDROP_NONE),
    ))
    return "painted"
