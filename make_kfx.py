#!/usr/bin/env python3
"""One-shot: bilingual EPUB  ->  split + keep-together  ->  KFX (Enhanced Typesetting).

This chains the tools you already have into a single command:

  [1/2]  split_paragraphs_kindle.py
         Split long EN/ES paragraph pairs into aligned chunks AND lock each
         English chunk to its Spanish translation so a page turn can't separate
         them.                                              ->  "<name> (split).epub"

  [2/2]  Calibre  `ebook-convert <split>.epub <name>.kfx`
         Uses the Calibre "KFX Output" plugin, which drives Kindle Previewer 3
         under the hood to produce a real KFX / Enhanced Typesetting file — the
         only format where the keep-together CSS is actually honored.
                                                            ->  "<name>.kfx"

Why orchestrate instead of "copying the plugin"?
------------------------------------------------
The KFX encoder is Amazon's CLOSED engine inside Kindle Previewer 3. There is no
open KFX writer to copy. The KFX Output plugin already wraps Previewer correctly;
this script simply runs your existing pipeline end-to-end.

Outputs
-------
* "<name> (split).epub"  — the improved EPUB. Upload THIS to KDP, or send it via
                           Send-to-Kindle; Amazon converts it to KFX server-side.
* "<name>.kfx"           — a sideloadable KFX for reading on your own Kindle.

Requirements
------------
* Calibre with the "KFX Output" plugin              (all platforms)
* Kindle Previewer 3:
    - Windows / macOS : installs and runs natively. No Wine, no display tricks.
    - Linux           : runs under Wine; needs a graphical session. When run
                        headless, this script auto-uses `xvfb-run` if installed.

Works the same on Windows, macOS, and Linux — the only platform-specific part is
how Kindle Previewer is launched, which Calibre's KFX Output plugin handles.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPLITTER = os.path.join(HERE, "split_paragraphs_kindle.py")


def step(msg):
    print(f"\n{msg}")


def plan_conversion(ebook_convert, src, dst, system, environ):
    """Decide how to launch the EPUB->KFX conversion for the current OS.

    Returns (cmd, note, blocked):
      cmd     - the command list to run, or None if we can't proceed
      note    - an informational line to print first, or None
      blocked - a reason code if blocked ('no-display'), or None

    On Windows/macOS Kindle Previewer runs natively, so we just call
    ebook-convert. On Linux it runs under Wine and needs a display; if there
    isn't one we fall back to xvfb-run, or report 'no-display'.
    """
    base = [ebook_convert, src, dst]

    if system in ("Windows", "Darwin"):
        # Native Kindle Previewer — no Wine, no display handling needed.
        return base, None, None

    # Linux (and any other Unix): Kindle Previewer runs under Wine. A clean Xvfb
    # screen is far more reliable for Wine than the desktop's own session —
    # especially on Wayland, where Wine often fails with "no driver could be
    # loaded" even when DISPLAY is set. So PREFER xvfb-run whenever it's
    # installed; only fall back to the live display if Xvfb isn't available.
    if shutil.which("xvfb-run"):
        return (["xvfb-run", "-a", "--server-args=-screen 0 1280x1024x24"] + base,
                "using a private virtual screen via xvfb-run (most reliable for Wine)",
                None)
    has_display = bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))
    if has_display:
        return (base,
                "no xvfb-run found — trying the desktop display directly "
                "(if Wine can't open a window, install 'xvfb')",
                None)
    return None, None, "no-display"


def print_no_display_help(epub_path):
    print("\n⚠ Can't convert to KFX here: on Linux, Kindle Previewer runs under Wine")
    print("  and needs a screen, but none is available — and 'xvfb-run' (a private")
    print("  virtual screen) isn't installed.\n")
    print("  Your finished EPUB (with the keep-together fix) is ready:")
    print(f"    {epub_path}\n")
    print("  Pick whichever is easiest:")
    print("   • EASIEST — Send-to-Kindle that EPUB, or upload it to KDP. Amazon")
    print("     converts it to KFX with Enhanced Typesetting for you (no Wine at all).")
    print("   • Local .kfx — install the virtual screen, then rerun:")
    print("       sudo apt install xvfb")
    print("   • Or run this on a Mac/Windows machine, where no Wine is needed.")


def print_failure_help(epub_path, system):
    print("\n✗ KFX conversion failed.")
    print(f"  The split/keep-together EPUB was still written:\n    {epub_path}")
    if system == "Linux":
        print("\n  On Linux this is almost always Wine being unable to open a window for")
        print("  Kindle Previewer (you'll see 'no driver could be loaded'). On Wayland")
        print("  this happens even when DISPLAY is set. The reliable fix is a private")
        print("  virtual screen (Xvfb):")
        print("       sudo apt install xvfb")
        print("     ...then rerun — this tool uses it automatically.")
        print("   - Also confirm Kindle Previewer 3 and Calibre's 'KFX Output' plugin are installed.")
    else:
        os_name = "macOS" if system == "Darwin" else system
        print(f"\n  On {os_name}, Kindle Previewer runs natively (no Wine). Check that:")
        print("   - Kindle Previewer 3 is installed, and")
        print("   - Calibre's 'KFX Output' plugin is installed (Preferences > Plugins).")
    print("\n  Easiest alternative on any system: Send-to-Kindle the EPUB above, or")
    print("  upload it to KDP — Amazon makes the KFX for you.")


def main():
    ap = argparse.ArgumentParser(
        description="Turn a bilingual EN/ES EPUB into a keep-together KFX in one command "
        "(Windows, macOS, or Linux)."
    )
    ap.add_argument("epub", help="Input .epub file.")
    ap.add_argument(
        "--keep-mode",
        choices=["flat", "wrap", "none"],
        default="flat",
        help="How to keep each EN/ES pair together (passed to the splitter). Default: flat.",
    )
    ap.add_argument(
        "--out",
        help="Output .kfx path (default: alongside the input, '<name>.kfx').",
    )
    ap.add_argument(
        "--epub-only",
        action="store_true",
        help="Stop after the split/keep-together step; don't convert to KFX. "
        "Use this if you only need the EPUB to upload to KDP.",
    )
    ap.add_argument(
        "--target-words",
        type=int,
        default=25,
        help="Approx words per chunk when splitting (default 25 = 'short'; "
        "~35 = 'medium'; ~12 for a 4-inch e-reader). Passed through to the splitter.",
    )
    ap.add_argument(
        "--min-split-words",
        type=int,
        default=70,
        help="Only split English paragraphs longer than this (default 70). Lower "
        "for small screens — e.g. ~20 for a 4-inch e-reader — so even moderately "
        "long pairs get broken up to fit one screen. Passed through to the splitter.",
    )
    args = ap.parse_args()

    epub = args.epub
    if not os.path.isfile(epub):
        sys.exit(f"No such file: {epub}")
    if not epub.lower().endswith(".epub"):
        sys.exit("Input must be an .epub file.")
    if not os.path.isfile(SPLITTER):
        sys.exit(f"Cannot find the splitter next to this script: {SPLITTER}")

    base, ext = os.path.splitext(epub)
    split_epub = base + " (split)" + ext
    out_kfx = args.out or (base + ".kfx")

    # ---- Step 1: split + keep-together ------------------------------------
    step("[1/2] Splitting long pairs and locking EN/ES pairs together ...")
    r = subprocess.run(
        [sys.executable, SPLITTER, epub, "--keep-mode", args.keep_mode,
         "--target-words", str(args.target_words),
         "--min-split-words", str(args.min_split_words)]
    )
    if r.returncode != 0:
        sys.exit("Splitter failed; aborting.")

    if os.path.isfile(split_epub):
        epub_for_convert = split_epub
        print(f"  -> {split_epub}")
    else:
        # The splitter writes nothing when there was nothing to change.
        print("  (no changes were needed; using the original EPUB for conversion)")
        epub_for_convert = epub

    if args.epub_only:
        print(f"\n✓ Done (EPUB only): {epub_for_convert}")
        print("  Upload this EPUB to KDP, or send it via Send-to-Kindle — Amazon "
              "converts it to KFX with Enhanced Typesetting.")
        return

    # ---- Step 2: EPUB -> KFX via Calibre + Kindle Previewer ----------------
    ebook_convert = shutil.which("ebook-convert")
    if not ebook_convert:
        sys.exit(
            "ebook-convert (Calibre) not found.\n"
            "Install Calibre and the 'KFX Output' plugin, or rerun with --epub-only "
            "and upload the EPUB to KDP instead."
        )

    system = platform.system()  # 'Linux', 'Darwin' (macOS), or 'Windows'
    cmd, note, blocked = plan_conversion(
        ebook_convert, epub_for_convert, out_kfx, system, os.environ
    )
    if blocked == "no-display":
        print_no_display_help(epub_for_convert)
        sys.exit(2)
    if note:
        print(f"  ({note})")

    # Wine wants XDG_RUNTIME_DIR; make sure it's set so Previewer starts cleanly.
    env = os.environ.copy()
    if system != "Windows" and not env.get("XDG_RUNTIME_DIR") and hasattr(os, "getuid"):
        cand = f"/run/user/{os.getuid()}"
        if os.path.isdir(cand):
            env["XDG_RUNTIME_DIR"] = cand

    step("[2/2] Converting EPUB -> KFX via Calibre + Kindle Previewer 3 ...")
    r = subprocess.run(cmd, env=env)

    if r.returncode == 0 and os.path.isfile(out_kfx):
        print(f"\n✓ Done: {out_kfx}")
        print(f"  (kept the intermediate EPUB too: {epub_for_convert})")
        print("\n  Final check: open the .kfx (or the EPUB) in Kindle Previewer 3 "
              "and confirm the green")
        print("  'Enhanced Typesetting' label is shown — that's when the "
              "keep-together rules are live.")
    else:
        print_failure_help(epub_for_convert, system)
        sys.exit(1)


if __name__ == "__main__":
    main()
