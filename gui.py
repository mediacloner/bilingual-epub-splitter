#!/usr/bin/env python3
"""Desktop GUI for the bilingual EN/ES EPUB splitter.

A small Tkinter window over the same pipeline the terminal menu drives:
pick an EPUB, choose how to keep each English/Spanish pair together, choose a
chunk length and an output format, then press Run. Output streams live into
the log panel; when it finishes you can open the folder that holds the result.

This is purely a front-end. All the real work happens in:
  * split_paragraphs_kindle.py  (split + keep-together engine)
  * make_kfx.py                 (one-shot orchestrator: split, then EPUB->KFX)

Run with:
    python3 gui.py

Requires Tkinter (Debian/Ubuntu: `sudo apt install python3-tk`).
"""

import glob
import os
import queue
import signal
import subprocess
import sys
import threading

import tkinter as tk
from tkinter import filedialog, font, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
MAKE_KFX = os.path.join(HERE, "make_kfx.py")

# Chunk-length presets, mirroring menu.py.
#   label -> (target_words, min_split_words)
# target_words    = approx words per chunk.
# min_split_words = only split English paragraphs longer than this.
CUSTOM_LABEL = "Custom…"
LENGTH_PRESETS = {
    "Tiny (12) — 4-inch e-readers (splits aggressively)": (12, 20),
    "Short (25) — 6-inch e-readers / phones": (25, 70),
    "Medium (35) — larger e-readers / tablets": (35, 70),
    "Large (50) — big tablets / fewest pieces": (50, 70),
    CUSTOM_LABEL: None,
}
DEFAULT_LENGTH = "Short (25) — 6-inch e-readers / phones"


def find_default_epub():
    """First non-generated .epub in the cwd, then next to the script."""
    for folder in (os.getcwd(), HERE):
        found = sorted(
            p for p in glob.glob(os.path.join(folder, "*.epub"))
            if not p.endswith(" (split).epub")
        )
        if found:
            return found[0]
    return ""


def open_in_file_manager(path):
    """Reveal `path` in the OS file manager. Best-effort, never raises."""
    try:
        if sys.platform.startswith("darwin"):
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class SplitterGUI:
    def __init__(self, root):
        self.root = root
        self.proc = None              # the running make_kfx subprocess, if any
        self.msg_queue = queue.Queue()  # worker thread -> UI thread
        self.last_output = None       # path produced by the last successful run
        self.cancelled = False        # set when the user cancels a run

        root.title("Bilingual EPUB Splitter")
        root.minsize(620, 560)

        self._build_widgets()
        self._poll_queue()

    # ---- layout -----------------------------------------------------------

    def _build_widgets(self):
        pad = {"padx": 14, "pady": 6}
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        # Header
        title_font = font.nametofont("TkDefaultFont").copy()
        title_font.configure(size=title_font.cget("size") + 6, weight="bold")
        ttk.Label(outer, text="Bilingual EPUB Splitter", font=title_font)\
            .grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text="Split long English/Spanish pairs and keep each translation on "
                 "the same page.",
            foreground="#666",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        # --- File chooser ---
        file_frame = ttk.LabelFrame(outer, text="EPUB file", padding=10)
        file_frame.grid(row=2, column=0, sticky="ew", pady=6)
        file_frame.columnconfigure(0, weight=1)
        self.file_var = tk.StringVar(value=find_default_epub())
        self.file_entry = ttk.Entry(file_frame, textvariable=self.file_var)
        self.file_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(file_frame, text="Browse…", command=self._browse)\
            .grid(row=0, column=1)

        # --- Keep-together mode ---
        keep_frame = ttk.LabelFrame(
            outer, text="Keep each English + Spanish pair together", padding=10)
        keep_frame.grid(row=3, column=0, sticky="ew", pady=6)
        self.keep_var = tk.StringVar(value="flat")
        ttk.Radiobutton(
            keep_frame, text="Flat — break rules on the paragraphs "
            "(no structural change; safest, start here)",
            variable=self.keep_var, value="flat",
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            keep_frame, text="Wrap — box each pair in a <div> "
            "(stronger, but can disturb drop caps / indentation)",
            variable=self.keep_var, value="wrap",
        ).grid(row=1, column=0, sticky="w")

        # --- Chunk length ---
        len_frame = ttk.LabelFrame(
            outer, text="Maximum chunk length", padding=10)
        len_frame.grid(row=4, column=0, sticky="ew", pady=6)
        len_frame.columnconfigure(1, weight=1)
        ttk.Label(len_frame, text="Preset:").grid(row=0, column=0, sticky="w")
        self.length_var = tk.StringVar(value=DEFAULT_LENGTH)
        self.length_combo = ttk.Combobox(
            len_frame, textvariable=self.length_var, state="readonly",
            values=list(LENGTH_PRESETS.keys()),
        )
        self.length_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.length_combo.bind("<<ComboboxSelected>>", self._on_length_change)

        self.custom_row = ttk.Frame(len_frame)
        self.custom_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(self.custom_row, text="Custom words per chunk:")\
            .grid(row=0, column=0, sticky="w")
        self.custom_words = tk.IntVar(value=25)
        self.custom_spin = ttk.Spinbox(
            self.custom_row, from_=1, to=200, width=6, textvariable=self.custom_words)
        self.custom_spin.grid(row=0, column=1, padx=8)
        self._on_length_change()  # set initial enabled/disabled state

        # --- Output format ---
        out_frame = ttk.LabelFrame(outer, text="Output format", padding=10)
        out_frame.grid(row=5, column=0, sticky="ew", pady=6)
        self.format_var = tk.StringVar(value="epub")
        ttk.Radiobutton(
            out_frame, text="EPUB — works in any reader; the file you upload to KDP",
            variable=self.format_var, value="epub",
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            out_frame, text="KFX — sideload onto a Kindle "
            "(needs Calibre + Kindle Previewer 3)",
            variable=self.format_var, value="kfx",
        ).grid(row=1, column=0, sticky="w")

        # --- Action row ---
        action = ttk.Frame(outer)
        action.grid(row=6, column=0, sticky="ew", pady=(10, 4))
        action.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(action, text="Run", command=self._on_run_clicked)
        self.run_btn.grid(row=0, column=0)
        self.progress = ttk.Progressbar(action, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        self.open_btn = ttk.Button(
            action, text="Open output folder", command=self._open_output,
            state="disabled")
        self.open_btn.grid(row=0, column=2)

        # --- Log panel ---
        log_frame = ttk.LabelFrame(outer, text="Output", padding=6)
        log_frame.grid(row=7, column=0, sticky="nsew", pady=(6, 0))
        outer.rowconfigure(7, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        mono = font.nametofont("TkFixedFont").copy()
        self.log = tk.Text(log_frame, height=10, wrap="word", font=mono,
                           background="#1e1e1e", foreground="#e6e6e6",
                           insertbackground="#e6e6e6", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = scroll.set

        # --- Status bar ---
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(outer, textvariable=self.status_var, foreground="#666")\
            .grid(row=8, column=0, sticky="w", pady=(6, 0))

    # ---- small UI helpers -------------------------------------------------

    def _browse(self):
        initial = os.path.dirname(self.file_var.get()) or os.getcwd()
        path = filedialog.askopenfilename(
            title="Select an EPUB",
            initialdir=initial,
            filetypes=[("EPUB files", "*.epub"), ("All files", "*.*")],
        )
        if path:
            self.file_var.set(path)

    def _on_length_change(self, _event=None):
        is_custom = self.length_var.get() == CUSTOM_LABEL
        state = "normal" if is_custom else "disabled"
        self.custom_spin.configure(state=state)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _resolve_length(self):
        """Return (target_words, min_split_words) for the current selection."""
        label = self.length_var.get()
        if label != CUSTOM_LABEL:
            return LENGTH_PRESETS[label]
        tw = int(self.custom_words.get())
        # For small targets (small screens) split more aggressively; otherwise
        # keep the historical threshold of 70. (Same rule as menu.py.)
        msw = max(2 * tw, 24) if tw < 20 else 70
        return tw, msw

    # ---- run / cancel -----------------------------------------------------

    def _on_run_clicked(self):
        if self.proc is not None:
            self._cancel()
        else:
            self._start()

    def _start(self):
        epub = self.file_var.get().strip()
        if not epub:
            self.status_var.set("Pick an EPUB file first.")
            return
        if not os.path.isfile(epub):
            self.status_var.set(f"File not found: {epub}")
            return
        if not os.path.isfile(MAKE_KFX):
            self.status_var.set(f"Cannot find make_kfx.py next to this app.")
            return

        if self.custom_spin.instate(["!disabled"]):
            try:
                self.custom_words.get()
            except tk.TclError:
                self.status_var.set("Enter a whole number of words per chunk.")
                return

        target_words, min_split_words = self._resolve_length()
        out_format = self.format_var.get()
        cmd = [
            sys.executable, MAKE_KFX, epub,
            "--keep-mode", self.keep_var.get(),
            "--target-words", str(target_words),
            "--min-split-words", str(min_split_words),
        ]
        if out_format == "epub":
            cmd.append("--epub-only")

        # Compute where the result will land so "Open output folder" works.
        base, ext = os.path.splitext(epub)
        if out_format == "kfx":
            self.last_output = base + ".kfx"
        else:
            split_epub = base + " (split)" + ext
            self.last_output = split_epub  # falls back to original if unchanged

        # Reset UI for a fresh run.
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._log("$ " + " ".join(
            f'"{c}"' if " " in c else c for c in cmd) + "\n\n")
        self.open_btn.configure(state="disabled")
        self.run_btn.configure(text="Cancel")
        self.progress.start(12)
        self.status_var.set("Running…")
        self.cancelled = False

        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()

    def _worker(self, cmd):
        """Run the pipeline, streaming combined stdout/stderr to the queue."""
        try:
            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if os.name != "nt":
                # Own process group so Cancel can kill grandchildren too
                # (ebook-convert / Kindle Previewer under xvfb).
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(cmd, **popen_kwargs)
            self.proc = proc
            for line in proc.stdout:
                self.msg_queue.put(("log", line))
            proc.wait()
            self.msg_queue.put(("done", proc.returncode))
        except Exception as exc:  # pragma: no cover - defensive
            self.msg_queue.put(("log", f"\n[error launching pipeline] {exc}\n"))
            self.msg_queue.put(("done", -1))

    def _cancel(self):
        proc = self.proc
        if proc is None:
            return
        self.cancelled = True
        self.status_var.set("Cancelling…")
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    self._on_finished(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _on_finished(self, returncode):
        self.proc = None
        self.progress.stop()
        self.run_btn.configure(text="Run")
        if self.cancelled:
            self.status_var.set("Cancelled.")
        elif returncode == 0:
            # KFX path and EPUB path both may produce a file; if the splitter
            # made no changes, fall back to the original EPUB for "open folder".
            if self.last_output and not os.path.isfile(self.last_output):
                self.last_output = self.file_var.get().strip()
            self.open_btn.configure(state="normal")
            name = os.path.basename(self.last_output) if self.last_output else ""
            self.status_var.set(f"Done ✓  {name}".strip())
        else:
            self.status_var.set(
                f"Finished with errors (exit {returncode}). See the log above.")

    def _open_output(self):
        if not self.last_output:
            return
        target = self.last_output
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        open_in_file_manager(folder or ".")


def main():
    root = tk.Tk()
    # Use a slightly more modern ttk theme when available.
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    SplitterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
