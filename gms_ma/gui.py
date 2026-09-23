"""Small desktop GUI (tkinter) for batch-indexing a MIDI folder.

Pick a folder, and every .mid/.midi below it (recursively) is processed into
loop files + the SQLite library while a progress bar shows files done / total.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

from .indexer import AnalysisConfig, classify_indexed_ex, gather_midi, run_index
from .store import Repository

DEFAULT_DB = str(Path("db") / "midi_loops.db")
DEFAULT_OUT = str(Path("out") / "loops")

# Logger levels -> prefix tag colour shown in the log pane.
LOG_LEVELS = {
    "info": "#8b94a3",
    "warning": "#f2c94c",
    "error": "#ff8f8f",
}


class IndexerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self._busy = False
        self._cancel = threading.Event()
        self._paths: list[str] = []   # chosen input: a folder OR explicit files
        self._files: list = []
        self._pending: list = []
        self._skipped: list = []
        self._corrupt: list = []
        self._corrupt_findings: list = []

        root.title("gms-ma — folder indexer")
        root.geometry("720x560")
        root.minsize(600, 480)

        pad = {"padx": 8, "pady": 4}
        self._build(pad)

    # ------------------------------------------------------------------ ui
    def _build(self, pad):
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        row0 = ttk.Frame(frm)
        row0.pack(fill="x", **pad)
        ttk.Label(row0, text="Input:").pack(side="left")
        self.folder = tk.StringVar()
        ent = ttk.Entry(row0, textvariable=self.folder, state="readonly")
        ent.pack(side="left", fill="x", expand=True, padx=6)
        self.btnBrowse = ttk.Button(row0, text="Choose folder…",
                                    command=self._browse)
        self.btnBrowse.pack(side="left")
        self.btnFiles = ttk.Button(row0, text="Choose MIDI files…",
                                   command=self._pick_files)
        self.btnFiles.pack(side="left", padx=6)

        row1 = ttk.Frame(frm)
        row1.pack(fill="x", **pad)
        ttk.Label(row1, text="Database file:").pack(side="left")
        self.db = tk.StringVar(value=DEFAULT_DB)
        ttk.Entry(row1, textvariable=self.db).pack(side="left", fill="x",
                                                   expand=True, padx=6)
        ttk.Label(row1, text="Loops out:").pack(side="left")
        self.out = tk.StringVar(value=DEFAULT_OUT)
        ttk.Entry(row1, textvariable=self.out).pack(side="left", fill="x",
                                                    expand=True, padx=6)

        row1b = ttk.Frame(frm)
        row1b.pack(fill="x", **pad)
        ttk.Label(row1b, text="Engine:").pack(side="left")
        self._eng = ttk.Combobox(row1b, values=("hybrid", "period", "repeat"),
                                 state="readonly", width=12)
        self._eng.current(0)
        self._eng.pack(side="left", padx=(4, 18))
        ttk.Label(row1b, text="Match lens:").pack(side="left")
        self._lens = ttk.Combobox(row1b, values=("pitch", "rhythm"),
                                  state="readonly", width=10)
        self._lens.current(0)
        self._lens.pack(side="left", padx=4)
        ttk.Label(row1b, text="(period: tiles · repeat: largest block · hybrid: both)",
                  foreground="#8b94a3").pack(side="left", padx=12)

        row1b2 = ttk.Frame(frm)
        row1b2.pack(fill="x", **pad)
        ttk.Label(row1b2, text="Options:").pack(side="left")
        self._stems = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1b2, text="Rhythm stems (piano + clap)",
                        variable=self._stems).pack(side="left", padx=(8, 24))
        self._motifs = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1b2, text="Extract motifs (grid-free)",
                        variable=self._motifs).pack(side="left")
        ttk.Label(row1b2, text="Harmony separation:").pack(side="left", padx=(12, 2))
        self._harm = ttk.Combobox(row1b2, values=("off", "top", "onset"),
                                  state="readonly", width=8)
        self._harm.current(1)
        self._harm.pack(side="left")
        ttk.Label(row1b2, text="(→ timeline 'View: Motifs' mode & loop library)",
                  foreground="#8b94a3").pack(side="left", padx=12)

        row1c = ttk.Frame(frm)
        row1c.pack(fill="x", **pad)
        self._reindex = tk.BooleanVar(value=False)
        ttk.Checkbutton(row1c, text="Re-index already processed",
                        variable=self._reindex,
                        command=self._refresh_processed).pack(side="left")
        self.lblIndexed = ttk.Label(row1c, text="", foreground="#8b94a3")
        self.lblIndexed.pack(side="left", padx=10)
        self._save_files = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1c, text="Save loops to disk",
                        variable=self._save_files).pack(side="right")
        self.db.trace_add("write", lambda *_: self._refresh_processed())

        # progress area
        box = ttk.LabelFrame(frm, text="Progress", padding=10)
        box.pack(fill="x", **pad)
        self.lblFound = ttk.Label(box, text="No folder selected")
        self.lblFound.pack(anchor="w")
        self.progress = ttk.Progressbar(box, mode="determinate")
        self.progress.pack(fill="x", pady=(6, 2))
        self.lblProg = ttk.Label(box, text="processed 0 / 0")
        self.lblProg.pack(anchor="w")
        self.lblCur = ttk.Label(box, text="", foreground="#8b94a3")
        self.lblCur.pack(anchor="w")

        # actions
        acts = ttk.Frame(frm)
        acts.pack(fill="x", **pad)
        self.btnRun = ttk.Button(acts, text="Process folder", command=self._start,
                                 state="disabled")
        self.btnRun.pack(side="left")
        self.btnStop = ttk.Button(acts, text="Stop", command=self._stop, state="disabled")
        self.btnStop.pack(side="left", padx=6)
        self.btnView = ttk.Button(acts, text="Open timeline viewer",
                                  command=self._open_viewer)
        self.btnView.pack(side="left")
        self.btnCheck = ttk.Button(acts, text="Check DB", command=self._check_db)
        self.btnCheck.pack(side="left", padx=6)
        self.btnFix = ttk.Button(acts, text="Fix corrupt", command=self._fix_corrupt,
                                 state="disabled")
        self.btnFix.pack(side="left")

        self.log = scrolledtext.ScrolledText(frm, height=10, state="disabled",
                                             font=("Consolas", 9))
        for level, colour in LOG_LEVELS.items():
            self.log.tag_config(level, foreground=colour)
        self.log.pack(fill="both", expand=True, **pad)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # --------------------------------------------------------------- actions
    def _browse(self):
        folder = filedialog.askdirectory(title="Choose a MIDI folder",
                                         initialdir=Path(self._paths[0]).parent
                                         if self._paths else ".")
        if not folder:
            return
        self._paths = [folder]
        files = gather_midi([folder])
        n = len(files)
        self.folder.set(folder)
        self._log(f"folder chosen: {folder}  ({n} MIDI file(s) found)")
        self.lblFound.config(text=f"Folder: {folder}  —  {n} MIDI file(s) found")
        self._refresh_processed()

    def _pick_files(self):
        files = filedialog.askopenfilenames(
            title="Choose one or more MIDI files",
            initialdir=Path(self._paths[0]).parent if self._paths else ".",
            filetypes=[("MIDI files", "*.mid *.midi"),
                       ("Standard MIDI", "*.mid"),
                       ("All files", "*.*")])
        if not files:
            return
        self._paths = list(files)
        n = len(files)
        shown = files[0] if n == 1 else files[0] + f"  (+{n - 1} more)"
        self.folder.set(shown)
        self._log(f"selected {n} MIDI file(s)")
        self.lblFound.config(text=f"Selected {n} MIDI file(s)")
        self._refresh_processed()

    def _refresh_processed(self):
        """Classify the chosen files against the DB and update the skip label."""
        db = self.db.get().strip() or DEFAULT_DB
        self._files = gather_midi(self._paths) if self._paths else []
        if self._files:
            self._pending, self._skipped, self._corrupt = \
                classify_indexed_ex(db, self._files)
        else:
            self._pending, self._skipped, self._corrupt = [], [], []
        total = len(self._files)
        skipped = len(self._skipped)
        corrupt = len(self._corrupt)
        parts = []
        if skipped and total:
            mode = "will be re-indexed" if self._reindex.get() else "will be skipped"
            parts.append(f"{skipped} of {total} file(s) already indexed — {mode}")
        if corrupt:
            parts.append(f"{corrupt} corrupt — will be re-indexed")
        self.lblIndexed.config(text="  ·  ".join(parts))
        if self._busy:
            return
        runnable = len(self._files) if self._reindex.get() else len(self._pending)
        self.btnRun.config(state="normal" if runnable else "disabled")

    def _start(self, files=None, force_reindex: bool = False):
        if files is None:
            if not self._paths:
                self._log("choose a folder or some MIDI files first", "warning")
                return
            files = self._files or gather_midi(self._paths)
        if not files:
            self._log("no .mid/.midi files in that selection", "warning")
            self.lblFound.config(text="No MIDI files found")
            return
        db = self.db.get().strip() or DEFAULT_DB
        pending, skipped, corrupt = classify_indexed_ex(db, files)
        reindex = force_reindex or bool(self._reindex.get())
        todo = files if reindex else pending
        if not todo:
            self.lblFound.config(
                text="All files already indexed — tick 'Re-index already processed' to run again")
            self._log("all files already indexed; enable 'Re-index already processed' to rerun",
                      "warning")
            return
        total = len(todo)
        if not reindex and skipped:
            for f in skipped:
                self._log(f"skip (already indexed) {f.name}", "warning")
        if corrupt:
            self._log("re-indexing corrupt song file(s): "
                      + ", ".join(f.name for f in corrupt), "warning")
        self._busy = True
        self._cancel.clear()
        self.btnRun.config(state="disabled")
        self.btnBrowse.config(state="disabled")
        self.btnFiles.config(state="disabled")
        self.btnCheck.config(state="disabled")
        self.btnFix.config(state="disabled")
        self.btnStop.config(state="normal")
        self.progress.config(maximum=max(1, total), value=0)
        self.lblFound.config(text=f"Processing {total} MIDI file(s)…")
        self.lblProg.config(text="processed 0 / %d" % total)
        out = self.out.get().strip() or DEFAULT_OUT
        cfg = AnalysisConfig(engine=self._eng.get(), lens=self._lens.get(),
                             stems=bool(self._stems.get()),
                             motifs=bool(self._motifs.get()),
                             harmony_split=self._harm.get(),
                             write_files=bool(self._save_files.get()))
        worker = threading.Thread(target=self._worker,
                                  args=(todo, db, out, cfg, total), daemon=True)
        worker.start()
        self._poll()

    def _check_db(self):
        """Scan the whole DB for corrupted songs and offer to fix them."""
        from . import doctor as doc

        db = self.db.get().strip() or DEFAULT_DB
        try:
            repo = Repository(db)
            findings = doc.scan(repo, deep=True)
            repo.close()
        except Exception as exc:  # pragma: no cover
            self._log(f"DB check failed: {exc}", "error")
            return
        self._corrupt_findings = findings
        if not findings:
            self._log("DB check: no corrupted songs found")
            self.btnFix.config(state="disabled")
            return
        for f in findings:
            self._log(f"corrupt: {f['name']} — " + "; ".join(f["issues"]), "warning")
        fix = doc.fixable(findings)
        self._log(f"DB check: {len(findings)} corrupt song(s), {len(fix)} fixable "
                  f"(re-indexable); unfixable ones need `doctor --delete`", "warning")
        self.btnFix.config(state="normal" if fix else "disabled")

    def _fix_corrupt(self):
        """Re-index the corrupt songs whose source files still exist."""
        from . import doctor as doc

        fix = doc.fixable(self._corrupt_findings)
        if not fix:
            self._log("nothing to fix", "warning")
            self.btnFix.config(state="disabled")
            return
        fix_names = {f["name"] for f in fix}
        for f in self._corrupt_findings:
            if f["name"] not in fix_names:
                self._log(f"cannot fix {f['name']} (source file missing) — "
                          f"remove it with `doctor --delete`", "warning")
        self._log(f"re-indexing {len(fix)} corrupt file(s)…")
        self._start(files=[Path(f["path"]) for f in fix], force_reindex=True)

    def _stop(self):
        self._cancel.set()
        self.btnStop.config(state="disabled")

    def _open_viewer(self):
        # Launch the viewer as a detached process so it keeps serving after the
        # GUI window is closed (an in-process daemon thread died with the GUI,
        # leaving the browser with ERR_CONNECTION_REFUSED).
        import subprocess
        import sys

        db = self.db.get().strip() or DEFAULT_DB
        cmd = [sys.executable, "-m", "gms_ma", "view", "--db", db, "--port", "8123"]
        try:
            flags = 0
            if sys.platform == "win32":
                flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
            subprocess.Popen(cmd, creationflags=flags, close_fds=True)
            self._log("timeline viewer starting: http://127.0.0.1:8123")
        except Exception as exc:  # pragma: no cover
            self._log(f"could not start viewer: {exc}", "error")

    # ------------------------------------------------------------- worker
    def _worker(self, files, db, out, cfg, total):
        def on_file(i, _t, path, status):
            self.q.put(("progress", i))
            self.q.put(("file", Path(path).name, status))

        def on_log(msg):
            if msg.startswith("skip"):
                level = "warning"
            elif msg.startswith("fail"):
                level = "error"
            else:
                level = "info"
            self.q.put(("log", msg, level))

        try:
            summary = run_index(files, db, out, cfg=cfg,
                                cancel=lambda: self._cancel.is_set(),
                                on_file=on_file, on_log=on_log)
            if summary.get("indexed"):
                self._refresh_library_cache(db)
            self.q.put(("done", summary))
        except Exception as exc:
            self.q.put(("error", str(exc)))

    def _refresh_library_cache(self, db):
        """Precompute the library facet/song cache after a successful index."""
        try:
            from . import library as lib

            repo = Repository(db)
            try:
                lib.refresh_library_cache(repo)
            finally:
                repo.close()
        except Exception:
            pass

    # ------------------------------------------------------------- pump
    def _poll(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "found":
                    pass
                elif kind == "progress":
                    i = rest[0]
                    self.progress.config(value=i)
                    self.lblProg.config(text=f"processed {i} / {self.progress['maximum']}")
                elif kind == "file":
                    name, status = rest
                    mark = {"ok": "✓", "skipped": "!", "failed": "✗"}.get(status, "?")
                    self.lblCur.config(text=f"{mark} {name}  ({status})")
                elif kind == "log":
                    self._log(rest[0], rest[1] if len(rest) > 1 else "info")
                elif kind == "done":
                    self._finish(rest[0])
                elif kind == "error":
                    self._log(str(rest[0]), "error")
                    self._busy = False
                    self._rearm_ui()
        except queue.Empty:
            pass
        if self._busy:
            self.root.after(80, self._poll)

    def _finish(self, summary):
        self._busy = False
        self._rearm_ui()
        self._refresh_processed()
        self._log(
            f"done: {summary['indexed']}/{summary['total']} files indexed in "
            f"{summary.get('elapsed', 0):.2f}s — "
            f"{summary['unique_patterns']} unique loops, "
            f"{summary['placements']} placements, "
            f"{len(summary['skipped'])} skipped")
        self.lblFound.config(text=f"Finished — {summary['indexed']} of {summary['total']} indexed")
        if summary["skipped"]:
            for s in summary["skipped"]:
                self._log(f"  skipped {s['file']}: {s['reason']}", "warning")

    def _rearm_ui(self):
        self.btnRun.config(state="normal")
        self.btnBrowse.config(state="normal")
        self.btnFiles.config(state="normal")
        self.btnStop.config(state="disabled")
        self.btnCheck.config(state="normal")
        from . import doctor as doc

        self.btnFix.config(
            state="normal" if doc.fixable(self._corrupt_findings) else "disabled")

    def _log(self, msg: str, level: str = "info"):
        if level not in LOG_LEVELS:
            level = "info"
        self.log.config(state="normal")
        self.log.insert("end", f"[{level}] ", level)
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _on_close(self):
        if self._busy:
            self.lblCur.config(text="still running — click Stop or wait before closing")
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        style.theme_use("vista")
    except Exception:
        pass
    IndexerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
