#!/usr/bin/env python3
"""Interactive menu for building the bilingual EN/ES book — no flags to remember.

Just run:

    python3 menu.py

It walks you through three choices:
  1. Pick the .epub file (from a list, or type a path)
  2. Max chunk length  (default: Short = 25 words)
  3. Output format     (EPUB, or KFX for Kindle sideloading)

Then it runs the splitter (which also locks each English paragraph to its
Spanish translation) and, if you chose KFX, the Calibre conversion — all for you.

This is just a friendly front-end over:
  * split_paragraphs_kindle.py  (the split + keep-together engine)
  * make_kfx.py                 (the one-shot orchestrator)
"""

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAKE_KFX = os.path.join(HERE, "make_kfx.py")

RULE = "=" * 54
THIN = "-" * 54


def ask_menu(title, options, default_index=0):
    """Print a numbered menu and return the chosen index (0-based)."""
    print(f"\n{title}")
    for i, label in enumerate(options, 1):
        mark = "   <- default" if (i - 1) == default_index else ""
        print(f"  {i}) {label}{mark}")
    while True:
        raw = input(f"Choice [{default_index + 1}]: ").strip()
        if raw == "":
            return default_index
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("  Please type a number from the list.")


def ask_text(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    if raw == "" and default is not None:
        return default
    return raw


def find_epubs():
    """List .epub files in the current folder (then next to the script),
    excluding generated '(split).epub' outputs."""
    for folder in (os.getcwd(), HERE):
        found = sorted(
            p for p in glob.glob(os.path.join(folder, "*.epub"))
            if not p.endswith(" (split).epub")
        )
        if found:
            return found
    return []


def choose_file():
    epubs = find_epubs()
    labels = [os.path.basename(p) for p in epubs] + ["(type a path manually)"]
    manual_index = len(labels) - 1
    idx = ask_menu("Step 1 — Select the EPUB file:", labels,
                   default_index=0 if epubs else manual_index)
    if idx == manual_index:
        path = os.path.expanduser(ask_text("Path to the .epub file"))
    else:
        path = epubs[idx]
    if not os.path.isfile(path):
        sys.exit(f"File not found: {path}")
    return path


def choose_keep_mode():
    idx = ask_menu(
        "Step 2 — How to keep each English+Spanish pair together:",
        [
            "Wrap each pair in a box  (recommended; strongest, Amazon's method)",
            "Flat: break rules on the paragraphs  (no change to structure)",
        ],
        default_index=0,
    )
    return "wrap" if idx == 0 else "flat"


def choose_length():
    """Return (target_words, min_split_words).

    target_words   - approx words per chunk.
    min_split_words- only paragraphs longer than this get split. Smaller screens
                     need this lower so pairs are broken up enough to fit one
                     screen (the keep-together rule can't hold a pair taller than
                     the screen).
    """
    idx = ask_menu(
        "Step 3 — Maximum chunk length (words per piece):",
        [
            "Tiny   (12)  - 4-inch e-readers (e.g. XTEink X4); splits aggressively",
            "Short  (25)  - 6-inch e-readers / phones",
            "Medium (35)  - larger e-readers / tablets",
            "Large  (50)  - big tablets / fewest, longest pieces",
            "Custom",
        ],
        default_index=0,
    )
    if idx == 0:
        return 12, 20
    if idx == 1:
        return 25, 70
    if idx == 2:
        return 35, 70
    if idx == 3:
        return 50, 70
    while True:
        v = ask_text("Enter max words per chunk", default="12")
        if v.isdigit() and int(v) > 0:
            tw = int(v)
            # For small chunk targets (small screens) also split more
            # aggressively; for normal/large targets keep the historical 70.
            if tw < 20:
                msw = max(2 * tw, 24)
                print(f"  (small chunks: will also split any pair longer than "
                      f"{msw} English words so it fits a small screen)")
            else:
                msw = 70
            return tw, msw
        print("  Please enter a positive whole number.")


def choose_format():
    idx = ask_menu(
        "Step 4 — Output format:",
        [
            "EPUB  - works in any reader; also the file you upload to KDP",
            "KFX   - sideload onto a Kindle (needs Kindle Previewer 3)",
        ],
        default_index=0,
    )
    return "epub" if idx == 0 else "kfx"


def main():
    print(RULE)
    print("  Bilingual EN/ES book builder")
    print(RULE)

    path = choose_file()
    keep_mode = choose_keep_mode()
    target_words, min_split_words = choose_length()
    out_format = choose_format()

    print("\n" + THIN)
    print("  Summary")
    print(f"    Input file:          {os.path.basename(path)}")
    print(f"    Keep pairs together: {keep_mode}")
    print(f"    Max chunk length:    {target_words} words")
    print(f"    Split pairs over:    {min_split_words} words")
    print(f"    Output:              {out_format.upper()}")
    print(THIN)
    if ask_text("Proceed? (Y/n)", default="Y").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return

    cmd = [
        sys.executable, MAKE_KFX, path,
        "--keep-mode", keep_mode,
        "--target-words", str(target_words),
        "--min-split-words", str(min_split_words),
    ]
    if out_format == "epub":
        cmd.append("--epub-only")

    print()
    rc = subprocess.run(cmd).returncode
    sys.exit(rc)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
