#!/usr/bin/env python3
"""Split long English/Spanish paragraph pairs AND keep each pair on one page.

This is a superset of `split_paragraphs_short.py`. It does everything that
script does (split long aligned EN/ES paragraph pairs into smaller chunks,
repack EPUBs), and then runs an extra "keep-together" pass so that a page
turn on a Kindle can never separate an English chunk from its Spanish
translation.

Why this matters
----------------
The book is a learning aid: every English paragraph is immediately followed
by its Spanish translation (Spanish has lang="es"). The value of the book is
that the two sit next to each other. If the e-reader drops a page break in the
gap between them — English at the bottom of one page, Spanish at the top of the
next — the reader loses the alignment exactly when they need it. The pair is
the atomic unit of the page: a break is allowed *between* pairs, never *inside*
one.

How it keeps a pair together
-----------------------------
There are two strategies, selectable with --keep-mode:

  flat  (DEFAULT) — add break-avoidance CSS directly to the paragraphs:
                    `page-break-after: avoid; break-after: avoid` on the
                    English <p>, and `page-break-before: avoid; break-before:
                    avoid` on its Spanish <p>. This changes NO document
                    structure, so it cannot disturb the book's existing CSS
                    (drop caps, paragraph indentation, etc.).

  wrap            — wrap each EN+ES pair in
                    `<div class="keeptogether" style="page-break-inside:avoid;
                    break-inside:avoid">`. This is Amazon's most directly
                    documented "group these together" idiom, but introducing a
                    <div> can break stylesheets that rely on `p + p` or
                    `:first-child` / `>` selectors. Use only if `flat` proves
                    insufficient and you've checked your layout still looks
                    right.

  none            — behave exactly like split_paragraphs_short.py (split only,
                    no keep-together pass).

Important limitations (please read)
-----------------------------------
* These CSS properties are honored by the KFX / "Enhanced Typesetting" engine
  on modern Kindles and in Kindle Previewer (look for the green "Enhanced
  Typesetting" check mark). On very old Kindles / legacy MOBI they may be
  ignored — there is no way to force a break to never happen.
* `avoid` cannot keep a pair together if the pair is taller than a full screen;
  the engine must break somewhere. Splitting long paragraphs (which this script
  also does) is what keeps pairs short enough for `avoid` to hold.
* ALWAYS verify the result in Kindle Previewer with Enhanced Typesetting on
  before publishing. The behavior is best-effort, not guaranteed.

The original file is never modified; EPUBs are written to "<name> (split).epub".
"""

import argparse
import os
import shutil
import sys
import tempfile
import zipfile

from bs4 import BeautifulSoup, NavigableString

# Abbreviations that contain a period but don't end a sentence.
ABBREV = {
    "Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Jr", "Sr", "Rev", "Hon",
    "Inc", "Ltd", "Co", "Corp", "Capt", "Lt", "Gen", "Col", "Sgt", "Mt",
    "vs", "etc", "cf", "al", "No", "Vol", "pp", "Fig", "fig",
    "Sra", "Sras", "Sres", "Dra", "Sto", "Sta",
}

OPENING_QUOTES = '"“«¿¡‘—–'  # incl Spanish ¿ ¡ « " ' — –

# Adaptive chunking by word budget.
#  - Each chunk aims for roughly TARGET_CHUNK_WORDS words.
#  - A sentence won't be split mid-sentence, so a very long single sentence
#    (e.g. 80 words) becomes its own chunk.
#  - Several short sentences combine until the chunk reaches the target.
TARGET_CHUNK_WORDS = 25
# Don't split paragraphs unless they're at least this many words AND have at
# least 2 sentences on both sides; otherwise it's not worth splitting.
SPLIT_IF_WORDS_GT = 70


CLOSING_MARKS = '"”»’\')]'

# Declarations added to keep a pair together (see module docstring). Both the
# legacy `page-break-*` and the modern unprefixed `break-*` forms are emitted,
# because Amazon's documentation references both and which one a given engine
# honors varies. No vendor prefixes — those are not in Amazon's supported set.
KEEP_AFTER_DECLS = "page-break-after:avoid;break-after:avoid"
KEEP_BEFORE_DECLS = "page-break-before:avoid;break-before:avoid"
WRAP_STYLE = "page-break-inside:avoid;break-inside:avoid"
WRAP_CLASS = "keeptogether"


def _skip_closers_and_footnotes(text, j):
    """Skip closing quotes/parens and inline footnote markers like [1]."""
    while j < len(text):
        c = text[j]
        if c in CLOSING_MARKS:
            j += 1
            continue
        if c == "[":
            k = j + 1
            while k < len(text) and text[k].isdigit():
                k += 1
            if k < len(text) and text[k] == "]" and k > j + 1:
                j = k + 1
                continue
        break
    return j


def is_sentence_boundary(text, idx):
    """Is text[idx] (a . ! or ?) a sentence-ending punctuation?"""
    if idx >= len(text) or text[idx] not in ".!?":
        return False
    # Allow trailing close-marks / footnote markers, then require whitespace.
    j = _skip_closers_and_footnotes(text, idx + 1)
    if j >= len(text):
        # End of string: a true boundary only if the caller is also looking
        # across nodes; in single-node mode, we can't know — be conservative.
        return False
    if not text[j].isspace():
        return False
    while j < len(text) and text[j].isspace():
        j += 1
    if j >= len(text):
        return True
    next_char = text[j]
    if not (next_char.isupper() or next_char in OPENING_QUOTES):
        return False
    # Check word preceding the period for known abbreviations.
    word_end = idx
    word_start = word_end
    while word_start > 0 and (text[word_start - 1].isalpha() or text[word_start - 1] == "."):
        word_start -= 1
    word = text[word_start:word_end].rstrip(".")
    if word in ABBREV:
        return False
    # Single capital letter + period could be an initial (J. K. Rowling) OR
    # part of an expression like "rayos X." — distinguish by looking at what
    # precedes the letter.
    if len(word) == 1 and word.isupper():
        pre = word_start - 1
        while pre >= 0 and text[pre] == " ":
            pre -= 1
        if pre < 0:
            return False  # start of text — assume initial
        pc = text[pre]
        if pc == "." or pc.isupper():
            return False  # chain of initials or after another capital
        # Preceded by lowercase letter or punctuation → real sentence end.
    return True


def find_sentence_starts(text):
    """Return indices marking the start of each new sentence (after . ! ?).

    A sentence start is the position of the first character of the next sentence
    (i.e., after the punctuation, any close-marks, and any whitespace).
    """
    starts = []
    for i, c in enumerate(text):
        if c not in ".!?":
            continue
        if not is_sentence_boundary(text, i):
            continue
        j = _skip_closers_and_footnotes(text, i + 1)
        while j < len(text) and text[j].isspace():
            j += 1
        if j < len(text):
            starts.append(j)
    return starts


def split_paragraph_into_sentences(p_tag):
    """Walk a <p>'s children and group them into sentences.

    Strategy: concatenate all descendant text into one string and find sentence
    breaks on the full string. Then walk the children, splitting NavigableString
    children at any break that falls inside them. Inline tags (footnote refs,
    page-break markers, <i>/<span>) stay inside whichever sentence contains
    them; if a break falls just after a tag, the next sentence begins in the
    following text node.
    """
    children = list(p_tag.children)

    # Build full text and remember each child's [start, end) in the full text.
    full_text = ""
    spans = []  # (start, end) per child
    for child in children:
        start = len(full_text)
        if isinstance(child, NavigableString):
            full_text += str(child)
        else:
            full_text += child.get_text()
        spans.append((start, len(full_text)))

    break_positions = find_sentence_starts(full_text)
    if not break_positions:
        # No internal breaks: whole paragraph is one sentence.
        sole = []
        for child in children:
            if isinstance(child, NavigableString):
                if str(child):
                    sole.append(str(child))
            else:
                sole.append(child)
        return [sole] if sole else []

    sentences = [[]]
    bp_iter = iter(break_positions)
    next_break = next(bp_iter, None)

    for child, (cs, ce) in zip(children, spans):
        if isinstance(child, NavigableString):
            text = str(child)
            local_cursor = 0
            while next_break is not None and next_break <= ce:
                local_pos = next_break - cs
                if local_pos < local_cursor:
                    # Break already passed (shouldn't happen normally).
                    next_break = next(bp_iter, None)
                    continue
                pre = text[local_cursor:local_pos]
                if pre:
                    sentences[-1].append(pre)
                # Start new sentence (only if current has content; avoids
                # creating a leading empty sentence when a break falls at idx 0).
                if sentences[-1]:
                    sentences.append([])
                local_cursor = local_pos
                next_break = next(bp_iter, None)
            tail = text[local_cursor:]
            if tail:
                sentences[-1].append(tail)
        else:
            sentences[-1].append(child)
            # Skip any breaks that fall strictly inside the tag (we don't
            # split inline tags); but a break exactly at ce (right after tag)
            # is a real boundary that belongs to the next text node, so don't
            # consume it here.
            while next_break is not None and next_break < ce:
                next_break = next(bp_iter, None)

    # Drop sentences that are entirely whitespace/empty.
    cleaned = []
    for sent in sentences:
        has_content = False
        for node in sent:
            if isinstance(node, str):
                if node.strip():
                    has_content = True
                    break
            else:
                if node.get_text(strip=True):
                    has_content = True
                    break
        if has_content:
            cleaned.append(sent)
    return cleaned


def node_word_count(node):
    if isinstance(node, str):
        return len(node.split())
    return len(node.get_text().split())


def sentence_words(sentence_nodes):
    return sum(node_word_count(n) for n in sentence_nodes)


def group_by_word_budget(sentences, target_words):
    """Group sentences into chunks aiming for target_words words each.

    A single sentence longer than the target stands alone in its own chunk.
    Several short sentences combine until they reach the target.
    """
    chunks = []
    current = []
    current_words = 0
    overshoot_limit = int(target_words * 1.4)
    min_carry = int(target_words * 0.5)
    for sent in sentences:
        w = sentence_words(sent)
        if not current:
            current = [sent]
            current_words = w
            continue
        # Should we close the current chunk and start fresh with this sentence?
        if current_words >= min_carry and current_words + w > overshoot_limit:
            chunks.append(current)
            current = [sent]
            current_words = w
        else:
            current.append(sent)
            current_words += w
    if current:
        chunks.append(current)
    return chunks


def cumulative_end_fractions(chunks):
    """For a list of chunks (each a list of sentences), return the cumulative
    word fraction at the END of each chunk. Last value is always 1.0."""
    words = [sum(sentence_words(s) for s in ch) for ch in chunks]
    total = sum(words) or 1
    cum = 0.0
    out = []
    for w in words:
        cum += w
        out.append(cum / total)
    return out


def split_to_match_boundaries(sentences, boundary_fractions):
    """Split sentences into len(boundary_fractions)+1 chunks whose boundaries
    fall as close as possible to the given target fractions of total words.

    Each chunk gets at least one sentence; the function never produces an
    empty chunk. If there aren't enough sentences for the requested number
    of chunks, fewer chunks are returned.
    """
    if not boundary_fractions:
        return [sentences]
    n_chunks = len(boundary_fractions) + 1
    if len(sentences) <= 1:
        return [sentences]
    if len(sentences) < n_chunks:
        # Not enough sentences for that many chunks — give one chunk per sentence.
        return [[s] for s in sentences]

    total = sum(sentence_words(s) for s in sentences) or 1
    cum_end = []
    cum = 0
    for s in sentences:
        cum += sentence_words(s)
        cum_end.append(cum / total)

    boundaries = []
    last = -1
    for ti, tf in enumerate(boundary_fractions):
        # Remaining chunks after this boundary (including the final one).
        chunks_remaining = n_chunks - ti - 1
        min_i = last + 1
        # Leave at least one sentence for each remaining chunk.
        max_i = len(sentences) - chunks_remaining - 1
        if max_i < min_i:
            max_i = min_i
        best_i = min_i
        best_diff = abs(cum_end[best_i] - tf)
        for i in range(min_i + 1, max_i + 1):
            diff = abs(cum_end[i] - tf)
            if diff < best_diff:
                best_diff = diff
                best_i = i
            elif cum_end[i] > tf and diff > best_diff:
                break
        boundaries.append(best_i)
        last = best_i

    chunks = []
    prev = 0
    for b in boundaries:
        chunks.append(sentences[prev:b + 1])
        prev = b + 1
    chunks.append(sentences[prev:])
    return chunks


def demote_class(class_attr):
    """Return a class list with drop-cap-related classes removed/renamed.

    The book's CSS attaches a drop cap to any element with class `pcalibre`
    (see `.pcalibre:first-letter`). The section-opener classes `pf` / `pf1`
    are paired with `pcalibre` in opening paragraphs. After a section-opener
    paragraph is split, only the first chunk should keep the drop cap; later
    chunks should look like ordinary body paragraphs (class `p` / `p1`).
    """
    if class_attr is None:
        return None
    classes = class_attr if isinstance(class_attr, list) else class_attr.split()
    demoted = []
    for c in classes:
        if c == "pcalibre":
            continue  # drop the drop-cap-inducing class
        if c == "pf":
            demoted.append("p")
        elif c == "pf1":
            demoted.append("p1")
        else:
            demoted.append(c)
    return demoted


def make_p_like(soup, template_p, sentence_groups, group_idx):
    """Build a new <p> based on template_p containing a flat list of nodes.

    For chunks after the first (group_idx > 0), demote drop-cap classes so the
    opening drop cap doesn't repeat on every chunk.
    """
    new_p = soup.new_tag("p")
    for attr, val in template_p.attrs.items():
        if attr == "class" and group_idx > 0:
            new_val = demote_class(val)
            if new_val:
                new_p[attr] = new_val
            continue
        new_p[attr] = val
    nodes = []
    for sent in sentence_groups:
        nodes.extend(sent)
    # Strip leading whitespace on the very first text node.
    if nodes and isinstance(nodes[0], str):
        nodes[0] = nodes[0].lstrip()
    if nodes and isinstance(nodes[-1], str):
        nodes[-1] = nodes[-1].rstrip()
    for node in nodes:
        if isinstance(node, str):
            new_p.append(NavigableString(node))
        else:
            new_p.append(node.extract() if node.parent else node)
    return new_p


def is_spanish_p(tag):
    return tag.name == "p" and tag.get("lang") == "es"


def is_english_p(tag):
    return tag.name == "p" and tag.get("lang") != "es"


def process_soup(soup):
    """Find English/Spanish <p> pairs and split long ones. Returns count of pairs split."""
    pairs_split = 0
    # Walk all <p> tags in document order.
    all_p = soup.find_all("p")
    i = 0
    while i < len(all_p) - 1:
        en_p = all_p[i]
        es_p = all_p[i + 1]
        if (
            is_english_p(en_p)
            and is_spanish_p(es_p)
            and en_p.parent is es_p.parent
        ):
            en_sents = split_paragraph_into_sentences(en_p)
            es_sents = split_paragraph_into_sentences(es_p)
            en_total_words = sum(sentence_words(s) for s in en_sents)
            if (
                en_total_words > SPLIT_IF_WORDS_GT
                and len(en_sents) >= 2
                and len(es_sents) >= 2
            ):
                # Decide chunking on the English side by word budget, then
                # align Spanish by matching English's chunk boundaries (as
                # fractions of total words). Each chunk pair covers the same
                # span of content; chunks may contain different numbers of
                # sentences in English vs Spanish.
                en_chunks = group_by_word_budget(en_sents, TARGET_CHUNK_WORDS)
                # If Spanish doesn't have enough sentences for that many
                # chunks, reduce on the English side too.
                if len(en_chunks) > len(es_sents):
                    target_fracs = [i / len(es_sents) for i in range(1, len(es_sents))]
                    en_chunks = split_to_match_boundaries(en_sents, target_fracs)
                n_chunks = len(en_chunks)
                if n_chunks >= 2:
                    en_boundary_fracs = cumulative_end_fractions(en_chunks)[:-1]
                    es_chunks = split_to_match_boundaries(es_sents, en_boundary_fracs)
                    n_chunks = min(n_chunks, len(es_chunks))
                    en_chunks = en_chunks[:n_chunks]
                    es_chunks = es_chunks[:n_chunks]
                    # Build new <p> tags: interleave EN then ES per chunk.
                    new_tags = []
                    for k in range(n_chunks):
                        new_tags.append(make_p_like(soup, en_p, en_chunks[k], k))
                        new_tags.append(make_p_like(soup, es_p, es_chunks[k], k))
                    # Insert new tags before en_p, then remove en_p and es_p.
                    for t in new_tags:
                        en_p.insert_before(t)
                    en_p.decompose()
                    es_p.decompose()
                    pairs_split += 1
                    # Restart scan because all_p is stale; simpler to rebuild.
                    all_p = soup.find_all("p")
                    # Advance past the new tags we just inserted.
                    i += 2 * n_chunks
                    continue
            i += 2
        else:
            i += 1
    return pairs_split


def _ensure_style_decls(tag, decls):
    """Append the declarations in `decls` (e.g. "a:b;c:d") to tag['style'],
    skipping any property already set on the tag. Existing style is preserved
    verbatim. Returns True if anything was added (idempotent across re-runs).
    """
    style = (tag.get("style") or "").strip()
    existing_props = set()
    for d in style.split(";"):
        d = d.strip()
        if ":" in d:
            existing_props.add(d.split(":", 1)[0].strip().lower())
    additions = []
    for d in decls.split(";"):
        d = d.strip()
        if not d:
            continue
        prop = d.split(":", 1)[0].strip().lower()
        if prop not in existing_props:
            additions.append(d)
            existing_props.add(prop)
    if not additions:
        return False
    if style and not style.endswith(";"):
        style += ";"
    tag["style"] = style + ";".join(additions)
    return True


def keep_pairs_together_flat(soup):
    """Add break-avoidance CSS to each EN<p> / ES<p> pair, no structural change.

    English <p> gets `page-break-after:avoid` (don't break after me); its
    Spanish <p> gets `page-break-before:avoid` (don't break before me). Together
    these tell the engine to keep the pair on one page. Breaks remain allowed
    between one pair and the next. Returns the number of pairs touched.
    """
    pairs = 0
    all_p = soup.find_all("p")
    i = 0
    while i < len(all_p) - 1:
        en_p = all_p[i]
        es_p = all_p[i + 1]
        if (
            is_english_p(en_p)
            and is_spanish_p(es_p)
            and en_p.parent is es_p.parent
        ):
            c1 = _ensure_style_decls(en_p, KEEP_AFTER_DECLS)
            c2 = _ensure_style_decls(es_p, KEEP_BEFORE_DECLS)
            if c1 or c2:
                pairs += 1
            i += 2
        else:
            i += 1
    return pairs


def keep_pairs_together_wrap(soup):
    """Wrap each EN<p> / ES<p> pair in <div class="keeptogether"> with
    page-break-inside:avoid. Returns the number of pairs wrapped.

    Note: this changes the document structure and may interact with stylesheet
    rules that rely on `p + p` or `:first-child`/`>` selectors. Prefer the
    flat mode unless you've confirmed your layout is unaffected.
    """
    pairs = 0
    all_p = soup.find_all("p")
    i = 0
    while i < len(all_p) - 1:
        en_p = all_p[i]
        es_p = all_p[i + 1]
        if (
            is_english_p(en_p)
            and is_spanish_p(es_p)
            and en_p.parent is es_p.parent
        ):
            parent = en_p.parent
            already_wrapped = (
                parent is not None
                and parent.name == "div"
                and WRAP_CLASS in (parent.get("class") or [])
            )
            if not already_wrapped:
                wrapper = soup.new_tag("div")
                wrapper["class"] = [WRAP_CLASS]
                wrapper["style"] = WRAP_STYLE
                en_p.insert_before(wrapper)
                wrapper.append(en_p.extract())
                wrapper.append(es_p.extract())
                pairs += 1
            # No <p> tags were added or removed (only moved), so document order
            # is unchanged and `all_p` indices stay valid — just step past them.
            i += 2
        else:
            i += 1
    return pairs


def apply_keep_together(soup, keep_mode):
    if keep_mode == "flat":
        return keep_pairs_together_flat(soup)
    if keep_mode == "wrap":
        return keep_pairs_together_wrap(soup)
    return 0


def process_file(path, keep_mode):
    """Split long pairs and keep pairs together in one file.

    Returns (pairs_split, pairs_kept_together). The file is rewritten only if
    either pass changed something.
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Use html.parser to keep things lenient; we'll re-add the XML decl.
    has_xml_decl = content.lstrip().startswith("<?xml")
    soup = BeautifulSoup(content, "html.parser")
    split_n = process_soup(soup)
    kept_n = apply_keep_together(soup, keep_mode)
    if split_n == 0 and kept_n == 0:
        return 0, 0
    out = str(soup)
    # html.parser may strip the XML declaration; restore if needed.
    if has_xml_decl and not out.lstrip().startswith("<?xml"):
        out = "<?xml version='1.0' encoding='utf-8'?>\n" + out
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return split_n, kept_n


def process_dir(text_dir, keep_mode):
    """Process every .html/.xhtml file under text_dir (recursively).

    Returns (files_changed, pairs_split, pairs_kept).
    """
    html_paths = []
    for root, _dirs, files in os.walk(text_dir):
        for name in files:
            if name.endswith(".html") or name.endswith(".xhtml"):
                html_paths.append(os.path.join(root, name))

    total_split = 0
    total_kept = 0
    total_files = 0
    for path in sorted(html_paths):
        split_n, kept_n = process_file(path, keep_mode)
        if split_n or kept_n:
            total_files += 1
            total_split += split_n
            total_kept += kept_n
            print(
                f"  {os.path.relpath(path, text_dir)}: "
                f"split {split_n} pair(s), kept {kept_n} pair(s) together"
            )
    return total_files, total_split, total_kept


def repack_epub(src_dir, out_path):
    """Zip src_dir into an EPUB: 'mimetype' first and stored, everything else deflated."""
    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w") as zf:
        mimetype = os.path.join(src_dir, "mimetype")
        if os.path.isfile(mimetype):
            zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for root, _dirs, files in os.walk(src_dir):
            for name in sorted(files):
                full = os.path.join(root, name)
                arc = os.path.relpath(full, src_dir)
                if arc == "mimetype":
                    continue
                zf.write(full, arc, compress_type=zipfile.ZIP_DEFLATED)


def process_epub(epub_path, keep_mode):
    """Extract an EPUB, split + keep-together its paragraphs, and write a new
    '… (split).epub'. The original file is left untouched.

    Returns (files_changed, pairs_split, pairs_kept).
    """
    base, ext = os.path.splitext(epub_path)
    out_path = base + " (split)" + ext
    tmp_dir = tempfile.mkdtemp(prefix="epub_split_")
    try:
        with zipfile.ZipFile(epub_path) as zf:
            zf.extractall(tmp_dir)
        total_files, total_split, total_kept = process_dir(tmp_dir, keep_mode)
        if total_split or total_kept:
            repack_epub(tmp_dir, out_path)
            print(f"\nWrote: {out_path}")
        else:
            print("\nNothing to change; no output written.")
        return total_files, total_split, total_kept
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    global TARGET_CHUNK_WORDS
    parser = argparse.ArgumentParser(
        description=(
            "Split long EN/ES paragraph pairs and keep each pair on one Kindle "
            "page so the Spanish translation never gets pushed to the next page."
        )
    )
    parser.add_argument(
        "target",
        help="A text directory of .html/.xhtml files, or a book.epub file.",
    )
    parser.add_argument(
        "--keep-mode",
        choices=["flat", "wrap", "none"],
        default="flat",
        help=(
            "How to keep each EN/ES pair together. "
            "'flat' (default): add break-avoidance CSS to the paragraphs, no "
            "structural change (safest). "
            "'wrap': wrap each pair in a <div> (stronger idiom, but may affect "
            "existing CSS). "
            "'none': split only, like split_paragraphs_short.py."
        ),
    )
    parser.add_argument(
        "--target-words",
        type=int,
        default=TARGET_CHUNK_WORDS,
        help="Approx words per chunk when splitting long paragraphs "
        "(default 25 = 'short'; ~35 = 'medium'). Larger = fewer, longer chunks.",
    )
    args = parser.parse_args()
    TARGET_CHUNK_WORDS = args.target_words

    target = args.target
    if os.path.isdir(target):
        total_files, total_split, total_kept = process_dir(target, args.keep_mode)
    elif os.path.isfile(target):
        total_files, total_split, total_kept = process_epub(target, args.keep_mode)
    else:
        sys.exit(f"No such file or directory: {target}")
    print(
        f"\nTotal: {total_split} pair(s) split, {total_kept} pair(s) kept "
        f"together (mode: {args.keep_mode}), across {total_files} file(s)."
    )


if __name__ == "__main__":
    main()
