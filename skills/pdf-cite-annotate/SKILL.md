---
name: pdf-cite-annotate
description: Annotate every quotation or citation taken from a PDF on local disk (~/reading, ~/dev, the wiki), e.g. after pdftotext or grep on a .pdf, by running pdf-annotate, which highlights the passage in the PDF and links it to the Logseq wiki.
---

# Annotate what you quote

Quote text from a local PDF to PSJ, a wiki page or a commit, and the passage gets a highlight in that PDF, cross-linked to the wiki. Brief subagents that quote to do the same.

## Steps

1. Pick the wiki page that cites the quote (`--wiki`, e.g. `CBPV leerpad`) and the source page `bron <Author> <Year> <Title>` (`--bron`; copy an existing name from `~/dev/experiments/wiki/pages/bron *`).
2. Run, with the verbatim quote (join distant fragments with `…`), a note saying why it was cited, the bare DOI when the register (`index.tsv` beside the PDF, or the Notion register) has one, and your live model name:
   ```bash
   <skill dir>/bin/pdf-annotate PDF "verbatim quote" "why cited" --wiki "<citing page>" --bron "bron <Author> <Year> <Title>" --doi <doi> --page <N> --author "Claude (<model id>)"
   ```
3. Done when stdout reads `added`, `updated` or `exists` with page and bbox, and `bron appended` or `bron exists`. Exit 1 (`not found`) means the quote is not in the PDF: fix the quote before citing it.
4. Commit the touched wiki pages as the wiki's `llm-wiki` skill prescribes (attributable `GIT_AUTHOR_*`, `Agent-*` trailers).

## Facts

- Rerunning is safe: an existing highlight gains the wiki link instead of a duplicate; the PDF is backed up once to `PDF.orig` and saved incrementally.
- The PDF note carries `logseq://graph/wiki?page=<urlencoded>` plus the plain page name; the bron block carries quote, pdf-page, `file://` link, `https://doi.org/` link and the citing page.
- `--page` is 1-based and only a search hint. pymupdf and pdftotext space differently (`UF` vs `U F`); the matcher ignores whitespace, so copy the quote from either.
- `pdf-cite` is the lighter sibling (highlight and comment, no wiki link); use `pdf-annotate`.
- Needs `nix` (the wrappers pull Python + PyMuPDF). The wiki lives at `$WIKI_DIR` (default `~/dev/experiments/wiki`).
