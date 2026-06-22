"""Split long English/Spanish paragraph pairs into smaller aligned chunks.

The book has alternating English and Spanish paragraphs (Spanish has lang="es").
For pairs where the English paragraph is "long" (>3 sentences), break both the
English and the Spanish paragraph into the same number of chunks, distributed
proportionally by sentence count. This keeps the chunks aligned by content even
when sentence counts differ between languages.
"""

import os
import re
import sys
from copy import copy

from bs4 import BeautifulSoup, NavigableString, Tag

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
TARGET_CHUNK_WORDS = 35
# Don't split paragraphs unless they're at least this many words AND have at
# least 2 sentences on both sides; otherwise it's not worth splitting.
SPLIT_IF_WORDS_GT = 70


CLOSING_MARKS = '"”»’\')]'


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
                    parent = en_p.parent
                    # Capture any whitespace text between en_p and es_p to preserve.
                    en_p.insert_before(*new_tags) if False else None  # not all bs4 supports multi insert
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


def process_file(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Use html.parser to keep things lenient; we'll re-add the XML decl.
    has_xml_decl = content.lstrip().startswith("<?xml")
    soup = BeautifulSoup(content, "html.parser")
    n = process_soup(soup)
    if n == 0:
        return 0
    out = str(soup)
    # html.parser may strip the XML declaration; restore if needed.
    if has_xml_decl and not out.lstrip().startswith("<?xml"):
        out = "<?xml version='1.0' encoding='utf-8'?>\n" + out
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return n


def main():
    text_dir = sys.argv[1]
    total_pairs_split = 0
    total_files = 0
    for name in sorted(os.listdir(text_dir)):
        if not name.endswith(".html") and not name.endswith(".xhtml"):
            continue
        path = os.path.join(text_dir, name)
        n = process_file(path)
        if n:
            total_files += 1
            total_pairs_split += n
            print(f"  {name}: split {n} pair(s)")
    print(f"\nTotal: {total_pairs_split} paragraph pairs split across {total_files} files.")


if __name__ == "__main__":
    main()
