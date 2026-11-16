"""pdf-annotate: highlight a quotation in a local PDF and cross-link it to the Logseq wiki.

Usage: pdf-annotate PDF "verbatim quote" "note" --wiki PAGE
           [--bron PAGE] [--doi DOI] [--page N] [--author NAME]

Builds on pdf-cite.py's matcher (ligatures, line breaks, whitespace). A quote may join
segments with an ellipsis, `a ... b`; every segment must sit on one page.

* The highlight's popup holds the note plus `Wiki: PAGE` and its logseq://graph/wiki URI
  (and the same for --bron, the source page that stores the quotation).
* Idempotent: a highlight already covering the quote gets the missing wiki link appended
  and is never duplicated; a second run changes nothing. Sticky notes beside it are
  updated the same way.
* The first change to a PDF copies it to PDF.orig; saves are incremental.
* With --bron, appends one block (quote, page, file link, DOI link, citing page, agent
  provenance) to $WIKI_DIR/pages/PAGE.md, creating the page when absent. The commit stays
  the agent's (see the llm-wiki skill).
* Prints `ACTION<TAB>page<TAB>bbox`; exits 1 when the quote is not in the PDF.
"""

import argparse
import datetime
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse

import pymupdf

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pdf_cite", HERE / "pdf-cite.py")
pdf_cite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pdf_cite)

WIKI_DIR = pathlib.Path(os.environ.get("WIKI_DIR", "~/dev/experiments/wiki")).expanduser()


def uri(page):
    """Logseq deep link for a wiki page name."""
    return "logseq://graph/wiki?page=" + urllib.parse.quote(page)


def wiki_lines(wiki, bron):
    """The cross-link text the PDF note must carry."""
    lines = [f"Wiki: {wiki} {uri(wiki)}"]
    if bron:
        lines.append(f"Bron: {bron} {uri(bron)}")
    return lines


def locate(doc, quote, first):
    """Return (page index, quads) for the first page holding every ellipsis segment."""
    segs = [s for s in re.split(r"\s*(?:…|\.\.\.)\s*", quote) if s]
    order = list(range(len(doc)))
    if first is not None:
        order.remove(first)
        order.insert(0, first)
    for i in order:
        found = [pdf_cite.hits(doc[i], s) for s in segs]
        if all(found):
            return i, [q for f in found for q in f]
    return None, []


def covers(annot, rect):
    """True when annot's rect overlaps rect over at least half of rect's area."""
    inter = annot.rect & rect
    return not inter.is_empty and inter.get_area() >= 0.5 * rect.get_area()


def beside(annot, rect):
    """True for a sticky note placed next to rect (annot.py put it just right of the quote)."""
    r = annot.rect
    return rect.y0 - 6 <= r.y0 <= rect.y1 + 6 and rect.x0 <= r.x0 <= rect.x1 + 40


def backup(path):
    """Copy path to path.orig once, before the first change."""
    orig = pathlib.Path(str(path) + ".orig")
    if not orig.exists():
        shutil.copy2(path, orig)


def with_links(content, links):
    """Append the link lines missing from content; None when nothing is missing."""
    missing = [ln for ln in links if uri_of(ln) not in content]
    return content.rstrip() + "\n\n" + "\n".join(missing) if missing else None


def uri_of(line):
    return line.split()[-1]


def model_author():
    """`Claude (<model slug>)` from the live session, else plain `Claude`."""
    try:
        slug = subprocess.run([HERE / "agent-model-name", "slug", ""], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        slug = ""
    return f"Claude ({slug})" if slug else "Claude"


def annotate(path, quote, note, wiki, bron, page, author):
    """Add or update the highlight; return (action, 1-based page, rect) or None."""
    doc = pymupdf.open(path)
    i, quads = locate(doc, quote, page - 1 if page else None)
    if i is None:
        return None
    pg = doc[i]  # keep the page alive: an annotation dies with its page object
    rect = quads[0].rect
    for q in quads[1:]:
        rect |= q.rect
    links = wiki_lines(wiki, bron)
    existing = {a.xref for a in pg.annots() if a.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT and covers(a, rect)}
    if existing:
        todo = []
        for a in pg.annots():
            if a.xref in existing or (a.type[0] == pymupdf.PDF_ANNOT_TEXT and beside(a, rect)):
                new = with_links(a.info.get("content", ""), links)
                if new:
                    todo.append((a, new))
        action = "updated" if todo else "exists"
        if todo:
            backup(path)
            for a, new in todo:
                a.set_info(content=new)
                a.update()
    else:
        backup(path)
        a = pg.add_highlight_annot(quads)
        stamp = datetime.date.today().isoformat()
        a.set_info(title=author, content="\n\n".join([note, *links, f"({stamp})"]))
        a.update()
        action = "added"
    if action != "exists":
        doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    return action, i + 1, rect


def append_bron(bron, path, quote, pno, note, wiki, doi, author):
    """Append one provenance-stamped block to the bron page; skip a quote already there."""
    page_file = WIKI_DIR / "pages" / f"{bron}.md"
    key = f'"{quote}" (pdf-p. {pno})'
    text = page_file.read_text() if page_file.exists() else ""
    if key in text:
        return "bron exists"
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "unknown")
    model = author[author.find("(") + 1 : -1] if "(" in author else "unknown"
    prov = (f"  agent-session:: [[agent-session/{sid}]]\n  agent-harness:: [[agent-harness/claude-code]]\n"
            f"  agent-model:: [[agent-model/{model}]]\n")
    file_link = f"[{pathlib.Path(path).name}](file://{urllib.parse.quote(str(path))})"
    doi_link = f" · [DOI](https://doi.org/{doi})" if doi else ""
    block = (f"- #LLM {key}. {note} Bron lokaal: {file_link}{doi_link} · geciteerd in [[{wiki}]]\n"
             f"  pdf-page:: {pno}\n{prov}")
    if not text:
        head = "type:: [[page-type/source]]\n" + (f"url:: https://doi.org/{doi}\n" if doi else "")
        head += "\n- Bron met geciteerde passages; elke passage staat ook als markering in het PDF zelf.\n" + prov
        text = head
    page_file.write_text(text.rstrip("\n") + "\n" + block)
    return "bron appended"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("quote")
    ap.add_argument("note")
    ap.add_argument("--wiki", required=True, help="wiki page that cites the quote")
    ap.add_argument("--bron", help="wiki source page ('bron Author Year Title') to append a block to")
    ap.add_argument("--doi", help="bare DOI, e.g. 10.1007/s10990-006-0480-6")
    ap.add_argument("--page", type=int, help="1-based page to search first")
    ap.add_argument("--author", default=None)
    a = ap.parse_args()
    path = str(pathlib.Path(a.pdf).expanduser().resolve())
    quote = " ".join(a.quote.replace("“", '"').replace("”", '"').split())
    author = a.author or model_author()
    res = annotate(path, quote, a.note, a.wiki, a.bron, a.page, author)
    if not res:
        print(f"not found: {quote[:80]}", file=sys.stderr)
        return 1
    action, pno, rect = res
    msg = f"{action}\t{pno}\t[{rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f}]"
    if a.bron:
        msg += "\t" + append_bron(a.bron, path, quote, pno, a.note, a.wiki, a.doi, author)
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
