# Bilingual EPUB Splitter (English / Spanish)

Tools for preparing a **bilingual English/Spanish EPUB used to learn English**.

The book alternates paragraphs: an English paragraph immediately followed by its
Spanish translation (the Spanish one carries `lang="es"`). For example:

```html
<p class="p">It was a bright cold day in April...</p>
<p lang="es" class="p1">Era un día luminoso y frío de abril...</p>
```

These scripts do two things:

1. **Split** long paragraph pairs into smaller, aligned chunks, so each short
   English chunk sits right next to its matching Spanish chunk.
2. **Keep each pair together** so a page turn on a Kindle can never separate an
   English chunk from its Spanish translation. *(New — see below.)*

---

## Why "keep together" matters

This book is a learning aid. Its whole value is that every English paragraph has
its Spanish translation right next to it, so the reader can glance down and check
the meaning.

On a Kindle, the page is filled automatically. Sometimes a page break lands in the
gap **between** an English paragraph and its Spanish translation — English at the
bottom of one page, Spanish at the top of the next. When that happens the reader
loses the alignment exactly when they need it.

The fix is to treat **each English + Spanish pair as one unbreakable unit**: a page
break is allowed *between* pairs, but never *inside* one.

---

## Requirements

- Python 3
- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/)

```bash
pip install beautifulsoup4
```

---

## The scripts

| Script | What it does |
|--------|--------------|
| **`menu.py`** | **Easiest. Interactive menu** — pick the file, chunk length, and EPUB/KFX output; it runs everything. No flags to remember. |
| `split_paragraphs.py` | Original. Splits long pairs only. Works on a folder of HTML files. |
| `split_paragraphs_short.py` | Original. Splits long pairs and can take an `.epub` directly (smaller chunks: ~25 words). |
| **`split_paragraphs_kindle.py`** | **Recommended.** Does everything `_short.py` does **plus** keeps each pair together so the Spanish never gets pushed to another page. |
| `make_kfx.py` | **One-shot.** Runs the splitter **and** converts the result to a KFX file in a single command (needs Calibre + KFX Output plugin + Kindle Previewer). |

> The two original scripts are kept as-is for reference. Use
> `split_paragraphs_kindle.py` for new work.

---

## Usage

### Easiest: the interactive menu

Just run, with no arguments:

```bash
python3 menu.py
```

It asks three simple questions and does the rest:

```
Step 1 — Select the EPUB file:        (pick from a list, or type a path)
Step 2 — Keep pairs together:         Wrap  <- default (strongest), or Flat
Step 3 — Maximum chunk length:        Short (25)  <- default, or Medium (35) / Custom
Step 4 — Output format:               EPUB  <- default, or KFX
```

Then it splits the book, locks every English paragraph to its Spanish
translation, and produces the EPUB (and the KFX if you chose it). This is the
recommended way — everything below is the same thing via the command line.

### Command line

`split_paragraphs_kindle.py` accepts either an `.epub` file **or** a folder of
`.html` / `.xhtml` files.

```bash
# An EPUB — writes a new "mybook (split).epub", leaves the original untouched
python3 split_paragraphs_kindle.py "mybook.epub"

# A folder of HTML files — edits the files in place
python3 split_paragraphs_kindle.py path/to/text/
```

### Choosing how pairs are kept together: `--keep-mode`

| Mode | What it does | When to use |
|------|--------------|-------------|
| **`flat`** *(default)* | Adds break-avoidance CSS **directly to the paragraphs** — `page-break-after:avoid` on the English `<p>` and `page-break-before:avoid` on its Spanish `<p>`. **No change to the document structure**, so it cannot disturb your existing styling (drop caps, indentation, etc.). | Almost always. Start here. |
| `wrap` | Wraps each pair in `<div class="keeptogether" style="page-break-inside:avoid;break-inside:avoid">`. A more direct "group these together" idiom. | Only if `flat` proves insufficient **and** you've checked your layout still looks right (see warning below). |
| `none` | Split only — behaves like `split_paragraphs_short.py`. | If you want the old behavior. |

```bash
python3 split_paragraphs_kindle.py "mybook.epub"                  # flat (default)
python3 split_paragraphs_kindle.py "mybook.epub" --keep-mode wrap # stronger, riskier
python3 split_paragraphs_kindle.py "mybook.epub" --keep-mode none # split only
```

The script prints what it changed, e.g.:

```
  OEBPS/ch1.xhtml: split 3 pair(s), kept 41 pair(s) together

Total: 3 pair(s) split, 41 pair(s) kept together (mode: flat), across 1 file(s).
Wrote: mybook (split).epub
```

Running it again on already-processed files is safe — it won't duplicate styles or
double-wrap anything.

---

## What the keep-together CSS looks like

**`flat` mode** (default — no structural change):

```html
<p class="p" style="page-break-after:avoid;break-after:avoid">English chunk.</p>
<p lang="es" class="p1" style="page-break-before:avoid;break-before:avoid">Spanish chunk.</p>
```

**`wrap` mode**:

```html
<div class="keeptogether" style="page-break-inside:avoid;break-inside:avoid">
  <p class="p">English chunk.</p>
  <p lang="es" class="p1">Spanish chunk.</p>
</div>
```

Both legacy (`page-break-*`) and modern (`break-*`) property names are emitted for
maximum compatibility. No vendor prefixes are used.

---

## ⚠️ Important: how reliable is this on Kindle?

These CSS rules are a **strong hint, not a guarantee**. Read this before publishing.

- **They work on the modern "Enhanced Typesetting" (KFX) engine** — current Kindles
  and Kindle apps, and Kindle Previewer. On very old Kindles / legacy MOBI they may
  be ignored. There is no CSS value that forces a break to *never* happen — `avoid`
  is the strongest there is.
- **A pair taller than one screen will still break** somewhere — the engine has no
  choice. This is exactly why the script also *splits* long paragraphs: smaller
  chunks fit on a page, so `avoid` can actually hold them together.
- **Amazon's own documentation is inconsistent** about `page-break-inside` (it's even
  listed as "ignored" in one of their guides), while `page-break-after:avoid` has
  clearer real-world support. That's why `flat` mode is the safe default.

### ✅ Always verify in Kindle Previewer

1. Open the generated `… (split).epub` in **Kindle Previewer**.
2. Confirm the green **"Enhanced Typesetting"** check mark is shown — that's when
   these keep-together rules are active.
3. Page through the book and confirm no Spanish translation is separated from its
   English paragraph by a page turn.

---

## ⚠️ Note on `wrap` mode and your book's CSS

Wrapping each pair in a `<div>` changes the document structure, which can interfere
with stylesheet rules that depend on paragraph position, for example:

- `p + p { text-indent: ... }` — paragraph indentation
- `body > p`, `:first-child`, `:first-letter` — drop caps and first-paragraph styling

If you use `wrap` mode and your indentation or drop caps look wrong afterward, switch
back to `flat` mode (the default), which never changes the structure.

---

## Making it actually work: getting the book into KFX (Enhanced Typesetting)

The keep-together CSS only "wakes up" on Amazon's modern **KFX / Enhanced
Typesetting** engine. KFX is a **proprietary Amazon format — no script or open
tool can create it directly.** You don't need to: this script's only job is to
produce the cleanest possible **EPUB**, and KFX is a separate, downstream
conversion you (or Amazon) run on that EPUB. **You never change the script for
this.**

Choose the path that matches your goal:

### One command, end to end: `make_kfx.py`

If you have Calibre (with the **KFX Output** plugin) and **Kindle Previewer 3**
installed, you can do the whole pipeline in a single command:

```bash
python3 make_kfx.py "The Worlds I See - Fei-Fei Li.epub"
```

This runs the splitter (keep-together) **and** converts the result to KFX,
producing two files:

- `… (split).epub` — the improved EPUB (upload this to KDP if publishing)
- `….kfx` — a sideloadable KFX for your own Kindle

Useful options:

```bash
python3 make_kfx.py "book.epub" --epub-only          # stop after the EPUB (for KDP)
python3 make_kfx.py "book.epub" --keep-mode wrap      # use wrap instead of flat
python3 make_kfx.py "book.epub" --out "Final.kfx"     # choose the .kfx name
```

> **It does not reimplement KFX** — that's impossible, because KFX is encoded by
> Amazon's closed engine inside Kindle Previewer. `make_kfx.py` just *orchestrates*
> the tools you already have (the splitter + Calibre's `ebook-convert`, which
> drives Kindle Previewer via the KFX Output plugin).
>
> **Works on Windows, macOS, and Linux.** The tool detects your OS:
> - **Windows / macOS:** Kindle Previewer 3 runs **natively — no Wine, nothing
>   special.** Just install Calibre + the KFX Output plugin + Kindle Previewer 3.
> - **Linux:** Kindle Previewer runs under **Wine**, which often *can't* open a
>   window on the desktop session — on Wayland you'll see `no driver could be
>   loaded` even when `DISPLAY` is set. The reliable fix is a private virtual
>   screen: **`sudo apt install xvfb`**. The tool then uses it automatically (it
>   prefers `xvfb-run` when present). Without it, the tool tries the desktop
>   display and, if Wine fails, stops with guidance instead of a crash. You can
>   always use `--epub-only` and let KDP / Send-to-Kindle do the conversion.

If you'd rather run the steps separately, the manual paths below do the same thing.

### Publishing on Amazon (KDP) — easiest, best result
1. Run the script → clean EPUB.
2. Upload the **EPUB** to **KDP**. Amazon converts it to KFX server-side and
   auto-enables Enhanced Typesetting for eligible reflowable books (this one
   qualifies). Your keep-together CSS then works for readers. You never produce
   KFX yourself.

### Reading on your own Kindle
- **Simplest:** send the **EPUB** through **Send-to-Kindle** — Amazon converts it
  to KFX for you. No extra tools.
- **For a local `.kfx` file:** use **Calibre** + the **KFX Output plugin** (by
  jhowell, MobileRead thread `t=272407`). Important: that plugin has **no engine
  of its own** — it *requires Amazon's Kindle Previewer 3 to be installed*
  (v3.38.0+) and just drives Previewer's command line to make a KPF, then
  repackages it into a sideloadable KFX. **On Linux, Kindle Previewer must run
  under Wine.** Then sideload the `.kfx` over USB.

> ⚠️ **Calibre's *built-in* "Convert books" → AZW3/MOBI does NOT give you this.**
> Calibre's own converter produces the old **KF8** format, whose engine does not
> support break-*avoid* (keep-together): it can *force* a page break
> (`page-break-before: always`) but cannot *prevent* one (`avoid`). So a
> natively-converted AZW3/MOBI will likely **still split your pairs**. Only the
> **KFX Output plugin** (which drives Kindle Previewer), or **KDP /
> Send-to-Kindle**, produces the KFX / Enhanced Typesetting file where this fix
> actually works.

> The related **KFX Input** plugin (`t=291290`) does the *reverse* (KFX → EPUB)
> and is not needed here.

### Confirm it worked
Open the converted book in **Kindle Previewer 3** and look for the green
**"Enhanced Typesetting"** label. If present, your keep-together rules are being
honored. If absent, the book fell back to the old KF8 engine and the rules may
not hold. Then page through and confirm no Spanish translation is ever separated
from its English.

### Dead ends to avoid
- **`kindlegen` is discontinued** (Aug 2020) and never produced KFX anyway — only
  the old MOBI/KF8 format. Don't build a pipeline around it.
- **MOBI uploads are no longer accepted** by KDP (reflowable) or Send-to-Kindle.
  **EPUB (EPUB 3 preferred) is the correct format today.**
- **Kindle Previewer cannot export a `.kfx` from its menus** — it only outputs
  KPF/MOBI/AZK. The sideloadable `.kfx` step is the third-party Calibre plugin
  above.

---

## How it works (brief)

1. **Split pass** — For each English/Spanish pair where the English paragraph is long
   (> 70 words, ≥ 2 sentences on both sides), both paragraphs are broken into the same
   number of chunks. The English side is chunked by a word budget (~25 words/chunk);
   the Spanish side is then split to match the English chunk boundaries, so the chunks
   stay aligned by content even when sentence counts differ. Drop-cap styling is only
   kept on the first chunk.
2. **Keep-together pass** — Every adjacent English→Spanish pair (split *or* not) is
   then locked together using the chosen `--keep-mode`.

The original file is never modified. EPUBs are written to a new
`<name> (split).epub`; the `mimetype` entry is stored correctly so the EPUB stays
valid.
