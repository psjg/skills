---
name: library-pdfs
description: >-
  Fetch paywalled or bot-blocked papers (ScienceDirect, Wiley, JSTOR, ACM,
  De Gruyter, IEEE, Springer, Cambridge, T&F) through PSJ's logged-in Chrome and
  the Utrecht University EZproxy, and open copies of the rest. Use when a source
  list has DOIs to collect, when curl/WebFetch get 403 or a Cloudflare
  challenge, or when a Notion source register has rows "nog te halen". Measured
  2026-10-31/11-01 on 50+ items.
---

# library-pdfs

Order of attempts, cheapest first. Stop at the first that lands a real PDF
(`file` says PDF, `pdftotext -l 1` shows the title). Never fake a browser
user agent in curl and never retry a 403 or a challenge page: that is bot-check
circumvention and the launch gate refuses it anyway.

## 0. Register first

Every item is a row in the Notion DB "Annotatielagen: bronnen" (or the project's
register). Confirm the DOI with Crossref before fetching (proposed rows from
chat models carry paraphrased titles; `api.crossref.org/works/<doi>`). Record
on the row: `Bezit`, `Lokaal pad`, `Halen`, and a `Notities` line with date,
source and page count. Files go to `~/reading/fpl/<topic>/` as
`author-year-short-title.pdf`.

## 1. Open copies with plain curl

- OpenAlex `works/https://doi.org/<doi>` → `best_oa_location.pdf_url`,
  `locations[].pdf_url`; Semantic Scholar `paper/DOI:<doi>?fields=openAccessPdf`.
- Author pages: Microsoft Research publication pages list a
  `wp-content/uploads/.../*.pdf`; ACL papers at `aclanthology.org/<id>.pdf`;
  ÚFAL tech reports at `ufal.mff.cuni.cz/techrep/trNN.pdf`; Georgia Tech
  `repository.gatech.edu/bitstreams/<id>/download`; course mirrors (ida.liu.se).
- CORE and ZORA often answer 403 or HTML to curl; skip them.

## 2. Chrome extension + UU proxy (the route that works)

PSJ logs in once per day at
`https://utrechtuniversity.idm.oclc.org/login?url=https://doi.org/<doi>`;
you cannot enter his credentials. Then, with the claude-in-chrome tools in one
tab group:

1. `navigate` to the proxy login URL with the DOI. IEEE DOIs break the proxy
   redirect: go straight to
   `ieeexplore-ieee-org.utrechtuniversity.idm.oclc.org/document/<arnumber>`.
2. `find` the PDF link ("View PDF", "ePDF", "Download Article (PDF)",
   "Download") and click it.
3. Two outcomes:
   - **Direct download** (De Gruyter button, T&F `?download=true`): lands in
     `~/Downloads` (Chrome default) with no dialog. Wait with a background
     `until ls …` loop, never with a sleep chain.
   - **Chrome's PDF viewer** (ScienceDirect `pdf.sciencedirectassets.com`,
     Wiley `/doi/pdfdirect/<doi>`, JSTOR `/stable/pdf/<id>.pdf`): click the
     viewer's download icon at (796, 28) in the 900-wide frame. Chrome's viewer
     always opens a macOS save sheet; PSJ presses Save (the sheet remembers the
     last folder). You cannot press it: osascript has no Accessibility grant and
     the computer-use tools treat browsers as read-only.
4. Check the file landed, rename, update the row.

`~/Downloads` is TCC-protected for the sandbox: list or move with
`osascript -e 'do shell script "…"'`.

Measured per publisher (UU licences, 2026-11-01):

| publisher | route | note |
|---|---|---|
| ACM 10.1145 | Control-Chrome fetch → local receiver | 15/15; see memory chrome-proxy-pdf-route |
| ScienceDirect | View PDF → viewer → Save | 7/7; signed asset URL is Cloudflare-gated, curl gets the challenge |
| Wiley | `/doi/pdfdirect/<doi>` in viewer → Save | the ePDF viewer's own button and `?download=true` never land |
| JSTOR | Download → "Accept and download" (ask PSJ first: terms) → viewer → Save | |
| De Gruyter | "Licensed", but no download ever starts through the proxy | leave to PSJ |
| IEEE Xplore, Cambridge JoL, Springer chapters, T&F books/HCI | no UU licence | author copies via step 1 |

## 3. What does not work

Browser pane: ScienceDirect refuses its user agent ("Claude/…"), it cannot
POST to localhost, and its anchor downloads vanish after approval. curl on a
signed ScienceDirect URL: Cloudflare challenge. Chrome `chrome://downloads` is
unreadable by the tools; ask PSJ or look at the folders.

## Hygiene

Keep polling loops few and kill them (`pkill -f 'until osascript'`) when done:
they made the fan spin. Close the extension's tabs at the end. One row per file
in Notion, one line in the hand-over note.
