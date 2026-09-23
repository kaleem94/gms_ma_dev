# gms_ma/clipboard.py

## supported / copy_file_to_clipboard <!-- ref:gms_ma/clipboard.py:14-90 -->
**Purpose**: Windows clipboard file copy via ctypes (`CF_HDROP`), letting a MIDI loop be "copied to the clipboard" as a real file for Ctrl+V into Explorer or a DAW.
**Why**: Avoids third-party dependencies; degrades gracefully on non-Windows (`supported()` returns False).
**Data Flow**: `path` → Windows clipboard (CF_HDROP).
**Relationships**: Used by `viewer._library_download` and `viewer._do_POST` clipboard endpoints.

### _DROPFILES / ctypes setup <!-- ref:gms_ma/clipboard.py:20-70 -->
**Purpose**: Define the `_DROPFILES` structure and load the Windows `kernel32`/`user32` DLLs for clipboard operations.
**Why**: The `CF_HDROP` format is the standard Windows mechanism for file drag-and-drop and clipboard copy. The `try/except` block at module level means the module loads safely on non-Windows platforms (all functions become no-ops or raise `RuntimeError`).
**Data Flow**: Module load → `_kernel32`/`_user32` handles or `None`.
**Relationships**: Used by `supported()` and `copy_file_to_clipboard()`.

```python
def supported() -> bool:
    return _user32 is not None

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
    ...
```