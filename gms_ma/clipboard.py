"""Windows clipboard *file* copy (CF_HDROP) via ctypes — like midi_out.py.

Lets a MIDI loop be "copied to the clipboard" as a real file so the user can
Ctrl+V it into Explorer or a DAW that accepts file drops.  Uses the same
no-dependency ctypes approach as :mod:`midi_out`.  On non-Windows or when the
APIs are unavailable the module degrades: :func:`supported` returns False and
:func:`copy_file_to_clipboard` raises.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

_CF_HDROP = 15
_GMEM_MOVEABLE = 0x0002
_GMEM_ZEROINIT = 0x0040


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),   # offset in bytes of the file list
        ("pt", _POINT),               # drop point (unused)
        ("fNC", wintypes.BOOL),       # client did a non-client-area drag
        ("fWide", wintypes.BOOL),     # TRUE => list is UTF-16
    ]


try:
    if sys.platform != "win32":
        raise OSError("CF_HDROP clipboard is Windows-only")
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    _GlobalAlloc = _kernel32.GlobalAlloc
    _GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _GlobalAlloc.restype = ctypes.c_void_p
    _GlobalLock = _kernel32.GlobalLock
    _GlobalLock.argtypes = [ctypes.c_void_p]
    _GlobalLock.restype = ctypes.c_void_p
    _GlobalUnlock = _kernel32.GlobalUnlock
    _GlobalUnlock.argtypes = [ctypes.c_void_p]
    _GlobalUnlock.restype = wintypes.BOOL
    _GlobalFree = _kernel32.GlobalFree
    _GlobalFree.argtypes = [ctypes.c_void_p]
    _GlobalFree.restype = ctypes.c_void_p

    _OpenClipboard = _user32.OpenClipboard
    _OpenClipboard.argtypes = [ctypes.c_void_p]
    _OpenClipboard.restype = wintypes.BOOL
    _EmptyClipboard = _user32.EmptyClipboard
    _EmptyClipboard.restype = wintypes.BOOL
    _SetClipboardData = _user32.SetClipboardData
    _SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    _SetClipboardData.restype = ctypes.c_void_p
    _CloseClipboard = _user32.CloseClipboard
    _CloseClipboard.restype = wintypes.BOOL
except Exception:  # pragma: no cover - platform dependent
    _kernel32 = _user32 = None


def supported() -> bool:
    return _user32 is not None


def _last_error_hint() -> str:
    code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
    return f" (error {code})"


def copy_file_to_clipboard(path: str | Path) -> None:
    """Put a file on the clipboard as a drop-able file (CF_HDROP)."""
    if not supported():
        raise RuntimeError("clipboard file copy is not available on this platform")
    fpath = str(Path(path).resolve())
    if not os.path.isfile(fpath):
        raise FileNotFoundError(fpath)

    payload = fpath.encode("utf-16-le") + b"\x00\x00"
    hdr_size = ctypes.sizeof(_DROPFILES)
    size = hdr_size + len(payload)

    h_mem = _GlobalAlloc(_GMEM_MOVEABLE | _GMEM_ZEROINIT, size)
    if not h_mem:
        raise RuntimeError(f"GlobalAlloc failed{_last_error_hint()}")
    ptr = _GlobalLock(h_mem)
    if not ptr:
        _GlobalFree(h_mem)
        raise RuntimeError(f"GlobalLock failed{_last_error_hint()}")
    try:
        df = _DROPFILES()
        df.pFiles = hdr_size
        df.fWide = True
        ctypes.memmove(ptr, ctypes.byref(df), hdr_size)
        ctypes.memmove(ptr + hdr_size, payload, len(payload))
    finally:
        _GlobalUnlock(h_mem)

    # The clipboard can briefly be owned by another window; retry a little.
    for _ in range(5):
        if _OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        _GlobalFree(h_mem)
        raise RuntimeError(f"OpenClipboard failed (clipboard busy?){_last_error_hint()}")
    try:
        _EmptyClipboard()
        if not _SetClipboardData(_CF_HDROP, h_mem):
            raise RuntimeError(f"SetClipboardData failed{_last_error_hint()}")
        h_mem = None  # ownership transferred to the clipboard
    finally:
        _CloseClipboard()
        if h_mem:
            _GlobalFree(h_mem)
