"""pdf-cite: highlight a quoted passage in a PDF and attach the agent's comment to it.

Usage: pdf-cite PDF "verbatim quote" "comment" [--author NAME] [--page N]

Finds the quote (whitespace-normalised; falls back to its first and last eight words when a line
break, hyphen or ligature splits it), adds a highlight annotation whose popup holds the comment,
and saves the PDF incrementally, so the original bytes stay and the annotation is appended.
Prints `page<TAB>matched-text` for each hit; exits 1 when the quote is not found, so a citing
agent cannot claim a quote the PDF does not hold.
"""

import argparse
import datetime
import sys

import pymupdf


LIGATURES = (("ffi", "ﬃ"), ("ffl", "ﬄ"), ("ff", "ﬀ"), ("fi", "ﬁ"), ("fl", "ﬂ"))


def ligated(text):
    """Spell text with the typographic ligatures TeX output often stores as single glyphs."""
    for plain, glyph in LIGATURES:
        text = text.replace(plain, glyph)
    return text


def plain(word):
    """Fold a word to compare: ligatures undone, punctuation and case dropped."""
    for lig, glyph in LIGATURES:
        word = word.replace(glyph, lig)
    return "".join(c for c in word.lower() if c.isalnum())


def by_words(page, quote):
    """Match quote as a run of words, which survives font changes, ligatures and line breaks."""
    want = [w for w in map(plain, quote.split()) if w]
    words = page.get_text("words")
    have = [plain(w[4]) for w in words]
    for i in range(len(have) - len(want) + 1):
        if have[i : i + len(want)] == want:
            return [pymupdf.Rect(w[:4]).quad for w in words[i : i + len(want)]]
    return []


def hits(page, quote):
    """Return the rectangles covering quote on page, or [] when it is not there."""
    for form in (quote, ligated(quote)):
        quads = page.search_for(form, quads=True)
        if quads:
            return quads
    quads = by_words(page, quote)
    if quads:
        return quads
    words = quote.split()
    if len(words) < 6:
        return []
    k = min(8, len(words) // 2)
    head, tail = " ".join(words[:k]), " ".join(words[-k:])
    a, b = page.search_for(head, quads=True), page.search_for(tail, quads=True)
    return a + b if a and b else []


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("quote")
    ap.add_argument("comment")
    ap.add_argument("--author", default="Claude")
    ap.add_argument("--page", type=int, help="1-based page to search first")
    args = ap.parse_args()
    quote = " ".join(args.quote.replace("“", '"').replace("”", '"').split())
    doc = pymupdf.open(args.pdf)
    order = list(range(len(doc)))
    if args.page:
        order.remove(args.page - 1)
        order.insert(0, args.page - 1)
    for i in order:
        page = doc[i]  # keep the page alive: an annotation dies with its page object
        quads = hits(page, quote)
        if not quads:
            continue
        annot = page.add_highlight_annot(quads)
        stamp = datetime.date.today().isoformat()
        annot.set_info(title=args.author, content=f"{args.comment}\n\n({stamp})")
        annot.update()
        doc.save(args.pdf, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        print(f"{i + 1}\t{quote[:80]}")
        return 0
    print(f"not found: {quote[:80]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
