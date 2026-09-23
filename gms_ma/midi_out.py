"""Minimal live MIDI output via the Windows Multimedia API (winmm) using ctypes.

Avoids third-party MIDI backends entirely (python-rtmidi currently has no
stable cp314 wheel and can crash on import).  Fallback seam: if this module is
not usable, viewers degrade to visual-only mode automatically.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_CALLBACK_NULL = 0
_MOM_CLOSE = 0x3C2

try:
    if sys.platform != "win32":
        raise OSError("winmm is Windows-only")
    _winmm = ctypes.WinDLL("winmm", use_last_error=True)
    _HANDLE = ctypes.c_void_p
    _RESULT = wintypes.UINT
    _midiOutGetNumDevs = _winmm.midiOutGetNumDevs
    _midiOutGetNumDevs.restype = wintypes.UINT
    _midiOutGetDevCapsW = _winmm.midiOutGetDevCapsW
    _midiOutGetDevCapsW.argtypes = [wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    _midiOutGetDevCapsW.restype = _RESULT
    _midiOutOpen = _winmm.midiOutOpen
    _midiOutOpen.argtypes = [ctypes.POINTER(_HANDLE), wintypes.UINT,
                             ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    _midiOutOpen.restype = _RESULT
    _midiOutShortMsg = _winmm.midiOutShortMsg
    _midiOutShortMsg.argtypes = [_HANDLE, wintypes.DWORD]
    _midiOutShortMsg.restype = _RESULT
    _midiOutClose = _winmm.midiOutClose
    _midiOutClose.argtypes = [_HANDLE]
    _midiOutClose.restype = _RESULT
except Exception:  # pragma: no cover - platform dependent
    _winmm = None


class _MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", ctypes.c_ushort),
        ("wPid", ctypes.c_ushort),
        ("vDriverVersion", wintypes.DWORD),
        ("szPname", ctypes.c_wchar * 32),
        ("wTechnology", ctypes.c_ushort),
        ("wVoices", ctypes.c_ushort),
        ("wNotes", ctypes.c_ushort),
        ("wChannelMask", ctypes.c_ushort),
        ("dwSupport", wintypes.DWORD),
    ]


def available() -> bool:
    return _winmm is not None


def list_outputs() -> list[str]:
    if not available():
        return []
    n = _midiOutGetNumDevs()
    out = []
    for i in range(n):
        caps = _MIDIOUTCAPSW()
        if _midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append(caps.szPname)
    return out


class WinMidiOut:
    """One open winmm output port."""

    def __init__(self, device_id: int = 0):
        if not available():
            raise RuntimeError("winmm MIDI not available on this platform")
        self._h = _HANDLE()
        res = _midiOutOpen(ctypes.byref(self._h), device_id, 0, 0, 0)
        if res != 0:
            raise RuntimeError(f"midiOutOpen failed with code {res}")

    @staticmethod
    def _pack(status: int, data1: int = 0, data2: int = 0) -> int:
        return (status & 0xFF) | ((data1 & 0x7F) << 8) | ((data2 & 0x7F) << 16)

    def note_on(self, channel: int, note: int, velocity: int) -> None:
        self._send(self._pack(0x90 | (channel & 0x0F), note, velocity))

    def note_off(self, channel: int, note: int) -> None:
        self._send(self._pack(0x80 | (channel & 0x0F), note, 0))

    def program_change(self, channel: int, program: int) -> None:
        self._send(self._pack(0xC0 | (channel & 0x0F), program))

    def pitch_bend(self, channel: int, value: int) -> None:
        value = max(0, min(16383, value))
        lsb = value & 0x7F
        msb = (value >> 7) & 0x7F
        self._send(self._pack(0xE0 | (channel & 0x0F), lsb, msb))

    def all_notes_off(self, channel: int | None = None) -> None:
        chans = [channel] if channel is not None else range(16)
        for ch in chans:
            self._send(self._pack(0xB0 | ch, 123, 0))

    def _send(self, dw_msg: int) -> None:
        res = _midiOutShortMsg(self._h, dw_msg)
        if res != 0:
            raise RuntimeError(f"midiOutShortMsg failed with code {res}")

    def close(self) -> None:
        try:
            _midiOutClose(self._h)
        except Exception:
            pass
