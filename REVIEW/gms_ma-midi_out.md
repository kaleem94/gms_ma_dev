# gms_ma/midi_out.py

## available / list_outputs / WinMidiOut <!-- ref:gms_ma/midi_out.py:14-100 -->
**Purpose**: Minimal live MIDI output via the Windows Multimedia API (winmm) using ctypes, avoiding third-party MIDI backends.
**Why**: `python-rtmidi` currently has no stable cp314 wheel and can crash on import; the winmm approach is a defensive fallback. If this module is not usable, viewers degrade to visual-only mode automatically.
**Data Flow**: `device_id, events` → live MIDI on a winmm output port.
**Relationships**: Used by `viewer._Player` for live audition; `viewer` checks `midi_out.available()` before enabling audio controls.

### available / list_outputs <!-- ref:gms_ma/midi_out.py:30-50 -->
**Purpose**: Check if the winmm MIDI backend is available and list all output device names.
**Why**: The `available()` function is used by the viewer to decide whether to enable audio controls in the UI. `list_outputs` lets the user select a specific MIDI device.
**Data Flow**: `()` → `bool`; `()` → `[str]`.
**Relationships**: Called by `viewer._Handler.do_GET` for `/api/midi-ports` and `viewer._Player.play()`.

### WinMidiOut <!-- ref:gms_ma/midi_out.py:52-100 -->
**Purpose**: Open a winmm output port and send MIDI messages (note on/off, program change, pitch bend, all notes off).
**Why**: The `_pack` static method encodes MIDI messages as 32-bit words for the `midiOutShortMsg` API. The `note_on` method ensures velocity is at least 1 (MIDI spec requires non-zero velocity for note on). Pitch bend values are converted from mido's signed convention (-8192..8191) to the 14-bit wire format (0..16383).
**Data Flow**: `device_id` → open port; `(ch, note, vel)` → `note_on`; `(ch, note)` → `note_off`; `(ch, prog)` → `program_change`; `(ch, value)` → `pitch_bend`.
**Relationships**: Used by `viewer._Player._run` for live MIDI playback.

```python
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
```