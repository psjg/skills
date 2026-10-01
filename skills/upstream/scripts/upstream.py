#!/usr/bin/env python3
"""upstream.py -- the upstream skill as an executable state machine.

An agent that files an issue, a comment or a PR on someone else's project
walks the skill's steps through this script, and the script is the only way
to post. Each transition is refused until its artifacts exist; the prose in
SKILL.md says *why*, this file says *whether*.

    upstream.py [--workdir DIR] init OWNER/REPO --channel issue|comment|pr --user @HANDLE
    upstream.py rules [--local CLONE]          fetch the house rules, grep them
    upstream.py rules classify --outcome ... --form ... [--note ...]
    upstream.py search --keywords "..."        issue and PR searches, logged
    upstream.py search mark N duplicate|related|unrelated "one clause"
    upstream.py claim add --text "..." --cmd "..." (--permalink URL | --no-permalink WHY)
    upstream.py claim list | claim rm N
    upstream.py env [--text "..."]
    upstream.py draft check draft.md
    upstream.py judge [--judge-cmd CMD] [--judge-model auto|jev|von|none]
    upstream.py judge override N --reason "..."
    upstream.py approve --by @HANDLE [--for FILE | --allow SPAN [--allow SPAN ...] --why "..."]
    upstream.py post --account MACHINE_ACCOUNT
    upstream.py fork --account MACHINE_ACCOUNT --branch B --patches DIR --base-tag TAG --author "..." --trailer "Assisted-By: ..."
    upstream.py status | graph --mermaid | graph --dot

Design
------
*State is derived, never set.* ``STATE.json`` is a ledger of what was done
(files fetched, searches run, claims with their outputs, check and judge
records, the approval). After every command the state is recomputed by
walking ``EDGES`` from the start and following each forward edge whose guard
holds against the ledger *and the draft file's current sha256*. Editing the
draft after approval therefore drops the state back to ``draft`` by itself:
approval binds to one exact text, not to a flag.

*One table.* ``EDGES`` drives the walk, ``status`` (a guard returns the list
of what is missing, in plain words) and ``graph``: the mermaid diagram in
SKILL.md is emitted from it and a test compares the two, so the picture can
not drift from the machine.

*Functional core, imperative shell.* Guards and draft checks are pure
functions of (ledger, text); the command functions at the bottom do the I/O
(gh, subprocesses, files) and hand values inward.

Hardness
--------
The gate is hard only for posts that go through this script. An agent can
still type ``gh issue create`` itself. Every gh call made here carries
``GH_UPSTREAM_SCRIPT=1`` in its environment, so a PreToolUse hook (or the
evals' gh shim) can refuse or flag any posting gh call that lacks it; only
that hook plus this script together make "the only way to post" true.

Judges and privacy
------------------
The judge ladder is optional and pluggable; with nothing configured the
symbolic checks alone pass the state and STATE.json says ``"judge": "not
judged"`` with the reason. (a) A decision model (Jev over OpenRouter's
decisions API with ``OPENROUTER_API_KEY``, or a local Von via ``import von``)
answers, per sentence, which ledger entry supports it (a Choice) and whether
that entry's output establishes it (a Noul). A hosted model receives only the
posted sentence and ledger entries reduced to (claim sentence, command line,
capped output, permalink), with paths, e-mail, user and host names scrubbed:
never the work dir, never shell history. The privacy pass runs in ``draft
check``, before any judge, so a judge reads no detail the pass flagged
unless the user allowed it; what the pass misses the judge does read, and
its finding of kind ``private`` always blocks.
``decision_requests`` is the one place such a request is built; a test pins its fields. (b) An adversarial LLM judge of
another model family: any ``--judge-cmd`` executable that reads the JSON
request on stdin and writes ``{"findings": [...]}`` on stdout. Adapters ship
in ``judges/``: ``codex.sh`` (the default when ``codex`` is on PATH),
``cursor.sh`` (cursor-agent with Grok) and ``stub.sh`` (no findings). The
verdict fails closed: no parseable ``findings`` object, a non-zero exit or
a timeout is one blocking finding, "judge output unparseable", never a pass.
Blocking findings hold the gate until the draft is fixed or the human
overrides them, and every verdict goes to ``judge.log``.

Stdlib only; Von is imported lazily and only when asked for.
"""

import argparse
import bisect
import difflib
import fcntl
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# --------------------------------------------------------------------------
# Constants. DISCLOSURE, MENTION and ENVELOPE are copied from the evals'
# grade.py, so a draft that passes here passes the grader's reading too.
# --------------------------------------------------------------------------

DISCLOSURE = re.compile(
    r"(\b(AI|LLM)\b|agent)[^\n]{0,100}\b(wrote|written|generated|drafted|prepared|authored|assist)"
    r"|\b(wrote|written|generated|drafted|prepared|authored|assist\w*)\b[^\n]{0,100}(\b(AI|LLM)\b|agent)",
    re.I,
)
MENTION = re.compile(r"(?<![\w/.`])@[A-Za-z0-9][\w-]*")
ENVELOPE = re.compile(r"(\*\*)?(To|From|Cc|Reply-To|Subject|Date|In-Reply-To|Title|Repo|Repository)(:\*\*|\*\*:|:)", re.I)
FOOTER = re.compile(r"upstream(-issue)?\b.*(@[0-9a-f]{7,40}\b|\bv\d+\b)")
FORBIDDEN = re.compile(r"\b(probably|plausibly|likely|should|I believe|appears to)\b", re.I)
RELATED = re.compile(r"^[\s*_>]*Related[*_\s]*:", re.M | re.I)
HTML_TAG = re.compile(r"</?[A-Za-z][\w-]*(\s[^>]*)?/?>")
FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.S | re.M)
POLICY_TERMS = [re.compile(r"\b(AI|LLM|DCO|WIP)\b"),
                re.compile(r"\b(generated|copilot|agents?|assisted-by|vouch\w*|signed-off-by|mentions?)\b", re.I)]
POLICY_NAME = re.compile(r"^(CONTRIBUTING|CODE_OF_CONDUCT|SECURITY)(\.\w+)?$", re.I)
OUTCOMES = ["welcome", "disclose-attest", "attest-explain", "issue-only", "refuse"]
MARKS = ["duplicate", "related", "unrelated"]
FINDING_KINDS = {"unsupported", "inference", "third-party-mention", "private", "irrelevant", "tone", "other"}
CAP = 16000  # bytes of command output kept per claim
HERE = Path(__file__).resolve().parent
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
JUDGE_PROMPT = """You are the maintainer of OWNER/REPO. The draft below will be posted to your
tracker by an AI agent, and you will have to verify whatever it asserts
without evidence. The report is a bug report, not a proof. Each ledger entry
is a command the agent ran and its captured output.

A sentence is SUPPORTED when a ledger entry's output shows it. That includes
a code line or a value shown by grep, git grep or a diff: a fact read from
the code by a command counts. Restating what an output shows is not an
inference.

The "contract_lines" are fixed boilerplate the skill requires (the
disclosure, the Related line, the footer, the environment paragraph). They
are checked elsewhere; never judge them or quote them in a finding.

Be adversarial about exactly these, and flag nothing else:
- unsupported: a sentence that no ledger entry bears on at all;
- inference: a causal or runtime claim whose only evidence is a code reading
  ("X crashes because Y" with no run showing it);
- unsupported: a number that appears in no ledger output;
- third-party-mention: a person mentioned or @-pinged who did not ask;
- private: what the report does not need about the user (a usage total, a
  plan or premium model name, a path, a user or host name), unless listed in
  "allowed", which the user approved; a ratio would make the same point;
  always blocking;
- tone: demands, blame, or pressure on the maintainer.
The "flagged" list holds sentences a decision model could not tie to the
ledger; confirm or dismiss each by the rules above.

Answer with strict JSON only, no prose around it:
{"findings": [{"quote": "<exact sentence>", "kind": "unsupported|inference|third-party-mention|private|irrelevant|tone|other", "blocking": true|false, "why": "<one clause>"}]}
A finding is blocking when a maintainer would be misled or would have to redo
the work. Return {"findings": []} when there is nothing."""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha(data) -> str:
    raw = data if isinstance(data, bytes) else json.dumps(data, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def norm(s: str) -> str:
    return " ".join(s.split())


def cap(s: str) -> str:
    return s if len(s) <= CAP else s[:CAP] + f"\n[... {len(s) - CAP} bytes cut]"


# --------------------------------------------------------------------------
# The machine: states, edges, guards. Pure functions of (ledger, draft sha).
# --------------------------------------------------------------------------

STATES = {
    "start": ("Issue, comment, test report or patch for someone else's project", "stop"),
    "rules": ("1 House rules fetched, grepped and classified per channel", "step"),
    "search": ("2 Search issues and PRs, open and closed; mark every hit", "step"),
    "evidence": ("3 Every claim carries a command, its output and a permalink", "step"),
    "draft": ("4-5 Write, disclosure first and footer last, nothing private unallowed, re-run every command", "step"),
    "judged": ("Judge: decision model, then an adversarial LLM of another family; unparseable blocks", "step"),
    "approval": ("6 The user's explicit yes to this exact text", "step"),
    "approved": ("7 Post as the machine account, verify the author, record the URL", "step"),
    "posted": ("Posted, author verified, URL in RECORD.md", "stop"),
    "forked": ("8 Rung 3: the patch series on the upstream tag, pushed to the machine account's fork", "step"),
    "security": ("Security problem: private channel, the human sends it", "stop"),
    "refused": ("AI content refused: the facts from claim list go to the user, no draft; fix in the user's fork, step 8", "stop"),
    "pr-refused": ("PRs closed until vouched: an issue with the diff instead", "stop"),
    "duplicate": ("Match found: comment there with --channel comment, or tell the user", "stop"),
}
ORDER = ["start", "rules", "search", "evidence", "draft", "judged", "approval", "approved", "posted"]

Guard = Callable[[dict, str | None], list[str]]


@dataclass(frozen=True)
class Edge:
    """One transition. ``kind`` is ``go`` (the forward step), ``stop`` (ends
    the flow when its guard returns nothing), ``side`` (a command allowed in
    ``src`` that leaves the state where it is: the fork) or ``back``
    (documentation only: the walk takes it by itself when a hash no longer
    matches). ``label`` names the subcommand that satisfies the guard."""
    src: str
    dst: str
    kind: str
    label: str
    guard: Guard = lambda L, h: []


def inputs_sha(L: dict) -> str:
    """What a draft check depends on besides the draft text."""
    return sha({"claims": [{k: c.get(k) for k in ("text", "cmd", "output", "exit", "permalink", "masks")} for c in L["claims"]],
                "marks": L["marks"], "no_mentions": L["rules"].get("no_mentions", False), "user": L["user"],
                "allows": sorted([a["what"], a["line"]] for a in L.get("allows", []))})


def need(cond, why: str) -> list[str]:
    return [] if cond else [why]


def g_rules(L, h):
    r = L["rules"]
    return (need(r.get("fetched"), "the house rules are not fetched: run `rules` (or `rules --local CLONE`)")
            + need(r.get("outcome"), "no outcome recorded: `rules classify --outcome ... --form ...`"))


def g_search(L, h):
    kinds = {s["kind"] for s in L["searches"] if s["exit"] == 0}
    unmarked = sorted(set(L["hits"]) - set(L["marks"]), key=int)
    return (need("issues" in kinds, "no successful issue search: `search --keywords \"...\"`")
            + need("prs" in kinds, "no successful PR search for a fix already proposed: `search --keywords \"...\"`")
            + need(not L.get("list") or "archive" in kinds, "no search of the list archive for proposed patches: `search --archive-cmd \"...\"`")
            + [f"hit #{n} is not marked: `search mark {n} duplicate|related|unrelated \"why\"`" for n in unmarked])


def g_evidence(L, h):
    bare = [c["id"] for c in L["claims"] if not (c.get("permalink") or c.get("no_permalink"))]
    return (need(L["claims"], "no claim yet: `claim add --text ... --cmd ...`")
            + [f"claim {i} has neither --permalink nor --no-permalink" for i in bare]
            + need(L.get("env"), "no environment recorded: `env` (detects) or `env --text \"...\"`"))


def g_draft(L, h):
    d = L.get("draft") or {}
    if not d:
        return ["no draft checked: `draft check FILE`"]
    return (need(d.get("sha") == h, "the draft changed since the last `draft check`")
            + need(d.get("inputs") == inputs_sha(L), "claims, marks, rules or allows changed since the last `draft check`")
            + need(d.get("passed"), f"`draft check` failed: {', '.join(d.get('failures', []))}"))


def g_judge(L, h):
    j = L.get("judge") or {}
    if not j or j.get("sha") != h or j.get("inputs") != inputs_sha(L):
        return ["the judge has not run on this draft: `judge`"]
    over = {o["n"] for o in j.get("overrides", [])}
    return [f"blocking finding {f['n']} ({f['kind']}): \"{f['quote'][:80]}\"; fix the draft or `judge override {f['n']} --reason ...`"
            for f in j.get("findings", []) if f.get("blocking") and f["n"] not in over]


def extension_diff(approved: str, current: str, channel: str) -> list[str]:
    """How ``current`` departs from the text the user approved, beyond what
    the skill itself requires on top of it: added blank lines, the
    disclosure as the first visible line, a `Related:` line and the footer
    as the last visible line. Empty means the approval still holds (the
    user approved the substance; the machine added its mandatory lines)."""
    vis = visible(split_title(current, channel)[1])
    first, last = (vis[0], vis[-1]) if vis else ("", "")
    bad = []
    for line in difflib.ndiff(approved.splitlines(), current.splitlines()):
        tag, text = line[:2], line[2:]
        added_ok = tag == "+ " and (not text.strip() or RELATED.match(text) or (text == first and DISCLOSURE.search(text))
                                    or (text == last and FOOTER.search(text)))
        if tag in ("- ", "+ ") and not added_ok:
            bad.append(line)
    return bad


def g_approve(L, h):
    a = L.get("approval") or {}
    if not a:
        return ["no approval: the user says yes to the text, then `approve --by @HANDLE [--for FILE]`"]
    if a.get("sha") == h:
        return []
    bad = extension_diff(a.get("text", ""), getattr(h, "text", ""), L["channel"]) if a.get("text") is not None and h else ["-"]
    return need(not bad, "the draft differs from the approved text beyond the disclosure, Related and footer lines; "
                         "show the user the new text and `approve` again:\n" + "\n".join(bad[:20]))


def g_post(L, h):
    return need(L.get("post"), "not posted: `post --account MACHINE_ACCOUNT`")


EDGES = [
    Edge("start", "rules", "go", "init OWNER/REPO --channel"),
    Edge("rules", "security", "stop", "rules classify --security", lambda L, h: need(L["rules"].get("security"), "-")),
    Edge("rules", "pr-refused", "stop", "outcome issue-only, channel pr",
         lambda L, h: need(L["rules"].get("outcome") == "issue-only" and L["channel"] == "pr", "-")),
    Edge("rules", "search", "go", "rules, rules classify", g_rules),
    Edge("search", "duplicate", "stop", "a hit marked duplicate",
         lambda L, h: need(L["channel"] != "comment" and "duplicate" in {m["mark"] for m in L["marks"].values()}, "-")),
    Edge("search", "evidence", "go", "search, search mark", g_search),
    Edge("evidence", "draft", "go", "claim add, env", g_evidence),
    Edge("draft", "refused", "stop", "outcome refuse: claim list to the user",
         lambda L, h: need(L["rules"].get("outcome") == "refuse", "-")),
    Edge("draft", "judged", "go", "draft check", g_draft),
    Edge("judged", "approval", "go", "judge", g_judge),
    Edge("approval", "approved", "go", "approve --by", g_approve),
    Edge("approved", "posted", "go", "post --account", g_post),
    Edge("approved", "forked", "side", "fork --patches --base-tag"),
    Edge("judged", "draft", "back", "blocking finding: edit the draft"),
    Edge("approval", "draft", "back", "changes"),
    Edge("approved", "draft", "back", "changes"),
    Edge("forked", "draft", "back", "cite the fork in the draft"),
]


def derive(L: dict, h: str | None) -> tuple[str, list[str]]:
    """Walk the edges from ``start``; return the state reached and what its
    forward edge still misses (empty at a stop state)."""
    s = "start"
    while True:
        outs = [e for e in EDGES if e.src == s and e.kind in ("go", "stop")]
        stop = next((e for e in outs if e.kind == "stop" and not e.guard(L, h)), None)
        if stop:
            return stop.dst, []
        go = next((e for e in outs if e.kind == "go"), None)
        if go is None:
            return s, []
        missing = go.guard(L, h)
        if missing:
            return s, missing
        s = go.dst


def mermaid() -> str:
    """The flowchart for SKILL.md, emitted from EDGES and STATES."""
    ids = {s: s.replace("-", "_") for s in STATES}
    out = ["flowchart TD"]
    for s, (label, kind) in STATES.items():
        out.append(f'    {ids[s]}(["{label}"])' if kind == "stop" else f'    {ids[s]}["{label}"]')
    for e in EDGES:
        arrow = {"back": "-.->", "side": "==>"}.get(e.kind, "-->")
        out.append(f'    {ids[e.src]} {arrow}|"{e.label}"| {ids[e.dst]}')
    return "\n".join(out) + "\n"


def dot() -> str:
    out = ["digraph upstream {", "  rankdir=TB; node [shape=box, fontname=Helvetica];"]
    for s, (label, kind) in STATES.items():
        out.append(f'  "{s}" [label="{label}"{", shape=oval" if kind == "stop" else ""}];')
    for e in EDGES:
        out.append(f'  "{e.src}" -> "{e.dst}" [label="{e.label}"{", style=dashed" if e.kind == "back" else ", style=bold" if e.kind == "side" else ""}];')
    return "\n".join(out + ["}"]) + "\n"


# --------------------------------------------------------------------------
# Draft checks: pure functions of the text and the ledger.
# --------------------------------------------------------------------------

def strip_fences(text: str) -> str:
    """Blank fenced code blocks, keeping the line count."""
    return FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def strip_code(text: str) -> str:
    """Fenced blocks and inline code spans removed: what is left is prose,
    where the word and mention rules apply."""
    return re.sub(r"`[^`\n]*`", "", strip_fences(text))


def split_title(text: str, channel: str) -> tuple[str | None, str]:
    """Issues and PRs carry their title as the draft's first line, ``# Title``;
    the posted body is the rest."""
    lines = text.splitlines()
    i = next((k for k, l in enumerate(lines) if l.strip()), len(lines))
    if channel in ("issue", "pr") and i < len(lines) and lines[i].startswith("# "):
        return lines[i][2:].strip(), "\n".join(lines[i + 1:]).strip("\n") + "\n"
    return None, text


def visible(body: str) -> list[str]:
    """Non-blank lines a reader sees first: a leading HTML comment and an
    envelope (To:, Subject:) are skipped, as grade.py's ``upfront`` does."""
    lines = [l for l in body.splitlines() if l.strip()]
    if lines and lines[0].lstrip().startswith("<!--"):
        end = next((k for k, l in enumerate(lines) if l.rstrip().endswith("-->")), -1)
        lines = lines[end + 1:]
    while lines and ENVELOPE.match(lines[0].strip()):
        lines = lines[1:]
    return lines


# --------------------------------------------------------------------------
# Privacy: what a post tells about the user that the maintainer does not
# need, matched per line of the draft file. Four kinds:
#   usage    a count of the user's own use, money, time spent. Counts are
#            sought only where a block names AI use (tokens, calls,
#            requests, sessions, prompts ...): a paragraph, a fenced block
#            of output, a line of a fenced block of source. There a number
#            of 10 or more is a count unless it is an identifier (glued to a
#            letter, `.`, `-`, `/`, `#`: versions, dates, refs), labelled
#            (`line 12`, `HTTP 429`), a measure (a unit, %, x, a rate per
#            second, minute or hour), per unit (after mean, median, max;
#            before "per call", "on average"), the N of "N of M" for a round
#            M, or round itself (one significant digit, or a power of two: a
#            limit, a repro parameter) on a line without "my", "our", "I
#            pay". A round total thus passes, a gap the judge's private lens
#            covers. Money counts in prose only (`$1` and awk's `$12` are
#            code); time spent only on such a line.
#   tier     a plan name, or a premium model with a version or a Claude
#            prefix: what the user pays for. Inside the disclosure's
#            parenthesis a span with no plan word is the authoring model,
#            named by rule; it passes unless the config sets "model":
#            "family".
#   private  a home path, an e-mail address, the config's private terms;
#            the login and host name in code, and in prose unless a plain
#            word ("will", "mark").
#   host     an IP address, a LAN or tailnet host name, a user@host prompt.
# Only the leading envelope (To:, Reply-To:) passes. Each flag holds the
# gate until the user allows it by name; an allow binds to the span and the
# line it stands in, so editing that line asks again. Pure: the config and
# the auto-detected names come in as ``P``. Linear in a line's length.
# --------------------------------------------------------------------------

USAGE_NOUN = re.compile(r"\b(?:tokens|calls|requests|sessions|transcripts|conversations|chats|prompts|messages|completions"
                        r"|invocations|(?:sub)?agents)\b", re.I)
SOURCE = set("python py javascript js jsx typescript ts tsx rust rs go c h cpp c++ cc java kotlin kt swift ruby rb php cs "
             "csharp scala lua perl pl r julia zig nim haskell hs ocaml elixir erlang clojure lisp scheme dart nix diff patch "
             "mermaid dockerfile makefile".split())
FENCE_OPEN = re.compile(r"^[ \t]*(```|~~~)[ \t]*([^\s`]*)")
RUN = re.compile(r"\d[\d,._]*")
MULT = re.compile(r"(?:[kKMB]|bn)(?![A-Za-z0-9])|e\d+\b")
CUR_BEFORE = re.compile(r"(?:[$€£]|\b(?:USD|EUR|GBP))\s?$")
CUR_AFTER = re.compile(r"\s?(?:[$€£]|(?:USD|EUR|GBP|dollars?|euros?|bucks)\b)")
UNIT = re.compile(r"\s?(?:%|×|x\b|(?P<time>min|mins|minutes?|h|hrs?|hours?|days?|wks?|weeks?|mo|months?|yrs?|years?)\b"
                  r"|(?:ms|µs|us|ns|s|secs?|seconds?|[KMGTP]i?B|bytes?|bits?|px|pt|em|[kMG]?Hz|fps|dpi|°[CF]?|cores?|threads?"
                  r"|lines?|chars?|characters?|rows?|columns?)\b)")
LABEL = re.compile(r"\b(?:HTTP|status|code|error|errno|exit|line|port|pid|PR|issue|build|version|section|RFC|step|row"
                   r"|column|col|offset|id|page)[\s:=#*`]*$", re.I)
STAT = re.compile(r"\b(?:mean|median|average|avg|typical|max|maximum|min|minimum|p\d\d)(?:\s+(?:of|is|was|at))?[\s:=(~≈*`]*$", re.I)
CLAUSE_END = re.compile(r"[,;()\[\]\d]|[.:](?:\s|$)")
PER_TIME = re.compile(r"\b(?:per|a|an|each|every)\s+(second|minute|hour|day|week|month|year)\b"
                      r"|/(s|sec|min|h|hr|d|day|wk|week|mo|month|yr|year)\b", re.I)
PER_UNIT = re.compile(r"\bper\s+\w|/[A-Za-z]|\bon\s+(?:average|avg)\b|\bapiece\b", re.I)
OF_M = re.compile(r"\s+(?:out\s+)?(?:of|in)\s+(?:the\s+|these\s+|those\s+|all\s+)?(\d[\d,]*)")
PERSONAL = re.compile(r"\b(?:my|our|mine|ours|spent|spending|pays?|paying|paid)\b|\b(?:I|we)\s+(?:use|used|spend)\b", re.I)
PLANS = [r"\b(?:Claude(?:\.ai)?|ChatGPT|Gemini|Copilot|Cursor|Grok|Perplexity|Windsurf|Codex)\s+"
         r"(?:Pro|Max|Plus|Ultra|Team|Business|Enterprise|Premium|Advanced|Heavy)\b\+?",
         r"\b(?:Pro|Max|Plus|Ultra|Premium|Team|Business|Enterprise)\s+(?i:plan|tier|subscription|seat)s?\b",
         r"\bMax\s?(?:5|20)x\b", r"\bSuperGrok\b", r"\b(?:API|[Uu]sage)\s+tier\s+\d\b", r"\b(?:o\d|gpt-[\w.]+)-pro\b",
         r"\bDeep\s?Think\b"]
MODELS = [r"(?i)\bclaude-(?:fable|opus)[\w.-]*", r"(?i)\b(?:Claude\s+)?(?:Fable|Opus)[\s-]?\d+(?:[.-]\d+)*\b",
          r"(?i)\bClaude\s+(?:Fable|Opus)\b"]
TIERS = PLANS + MODELS
PLAN_WORD = re.compile(r"\b(?:plan|tier|subscription|seat|Pro|Max|Plus|Ultra|Team|Business|Enterprise|Premium|Heavy|Advanced)\b"
                       r"|\dx\b", re.I)
PRIVATE = [re.compile(r"(?<![\w.~-])/(?:Users|home)/(?!(?:user|me|you|runner|name|username|example|USER|Shared)\b)[\w.-]+"),
           re.compile(r"/mnt/[a-z]/Users/[\w.-]+"), re.compile(r"(?<![\w.-])/Volumes/[\w.-]+"), re.compile(r"\b[A-Za-z]:\\Users\\[\w.-]+"),
           re.compile(r"/(?:private/)?var/folders/[\w/.+-]+"), re.compile(r"-Users-[\w.]+-"), re.compile(r"(?<![\w/~])~[a-z][\w.-]*(?=/)")]
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HOSTS = [re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.])"),
         re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){5,7}[0-9A-Fa-f]{1,4}(?![\w:])"),
         re.compile(r"(?<![\w:])f(?:e[89ab]|[cd][0-9a-f])[0-9a-f]{0,2}::?[0-9a-f:]*[0-9a-f](?:%\w+)?", re.I),
         re.compile(r"(?<![\w.-])[\w.-]+@[\w.-]+:[~/]"), re.compile(r"(?<![\\\w])\\\\[\w.-]+\\")]
HOSTNAME = re.compile(r"(?<![\w.-])[\w-]+(?:\.[\w-]+)+")
LAN = (".local", ".lan", ".localdomain", ".home.arpa", ".ts.net", ".internal", ".intranet", ".fritz.box", ".corp")
SAFE_IP = re.compile(r"^(127\.|0\.0\.0\.0$|255\.255\.255\.255$|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)")
COMMON_NAMES = {"root", "user", "admin", "runner", "dev", "test", "guest", "vagrant", "pi", "ec2-user", "nobody", "localhost",
                "macbook", "macbook-pro", "macbook-air", "imac", "mac-mini", "mac-studio", "raspberrypi", "ubuntu", "debian", "fedora"}
KINDS = ("usage", "tier", "private", "host")


def parse_number(run: str) -> tuple[float, bool] | None:
    """``run`` (1,234 / 1.234.567 / 1_234 / 44,1 / 0.40) as (value,
    integral); None for a version or a list (0.4.1, 1,2,3)."""
    if "_" in run:
        parts = run.split("_")
        return (float("".join(parts)), True) if parts[0].isdigit() and all(len(p) == 3 and p.isdigit() for p in parts[1:]) else None
    parts, seps = re.split(r"[.,]", run), re.findall(r"[.,]", run)
    if not seps:
        return float(run), True
    grouped = lambda ps: len(ps[0]) <= 3 and all(len(p) == 3 for p in ps[1:])
    if len(set(seps)) == 1:
        if len(parts) > 2:
            return (float("".join(parts)), True) if grouped(parts) else None
        if seps[0] == "," and grouped(parts):
            return float("".join(parts)), True
        return float(f"{parts[0]}.{parts[1]}"), False
    if len(set(seps[:-1])) == 1 and seps[-1] != seps[0] and grouped(parts[:-1]):  # 1,234.5 or 1.234,5
        return float("".join(parts[:-1]) + "." + parts[-1]), False
    return None


def is_round(v: float) -> bool:
    """One significant digit, or a power of two from 16: a number a person
    chose (a limit, a repro parameter), not one a run measured."""
    n = round(v)
    return abs(n - v) < 1e-6 * max(1.0, v) and (len(str(n).rstrip("0")) <= 1 or (n >= 16 and n & (n - 1) == 0))


def usage(line: str, code: bool, counting: bool) -> list[tuple[int, int]]:
    """The spans of ``line`` that tell how much the user uses or pays: see
    the header above. One pass over the numbers, each looked at through a
    bounded window, so the cost is linear in the line."""
    personal = bool(PERSONAL.search(line))
    inline = [c.span() for c in re.finditer(r"`[^`\n]*`", line)]
    starts = [x for x, _ in inline]
    in_code = lambda a: (i := bisect.bisect_right(starts, a) - 1) >= 0 and a < inline[i][1]
    out = []
    for m in RUN.finditer(line):
        raw = m[0].rstrip(",._")
        a, b = m.start(), m.start() + len(raw)
        p = line[a - 1] if a else " "
        if p.isalnum() or p in "_.-/#@\\^" or (p == ":" and a > 1 and line[a - 2].isdigit()):
            continue  # x86, v1, 2026-03-02, #12, /issues/12, 12:30
        parsed = parse_number(raw)
        if parsed is None:
            continue
        v, integral = parsed
        mult = MULT.match(line, b)
        if mult:
            v *= {"k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9, "bn": 1e9}.get(mult[0]) or 10 ** int(mult[0][1:])
            b, integral = mult.end(), True
        elif (line[b:b + 1].isalpha() and not UNIT.match(line, b)) or line[b:b + 1] == "_" or re.match(r"[-:/.]\w", line[b:b + 2]):
            continue  # 26A428, 3_a, 2026-03, 12:30, 1/2, 3.x
        before, after = line[max(0, a - 40):a], line[b:b + 80]
        cur, cur_after = CUR_BEFORE.search(before), CUR_AFTER.match(after)
        end = CLAUSE_END.search(after)
        clause = after[:end.start() if end else len(after)]
        per_time = PER_TIME.search(clause)
        per_unit = not per_time and bool(PER_UNIT.search(clause)) or bool(STAT.search(before))
        if cur or cur_after:  # money, in prose only
            if not (code or in_code(a) or per_unit) and (personal or (v >= 10 and not is_round(v))):
                out.append((a - len(cur[0]) if cur else a, b + (0 if cur else cur_after.end())))
            continue
        unit = UNIT.match(after)
        if unit:
            if unit["time"] and personal:  # time spent
                out.append((a, b + unit.end()))
            continue
        if not counting or v < 10 or not integral or LABEL.search(before) or per_unit:
            continue
        if per_time and per_time[1 if per_time[1] else 2].lower() in ("second", "minute", "hour", "s", "sec", "min", "h", "hr"):
            continue  # a throughput or a rate limit, not a volume
        of = OF_M.match(after)
        whole = parse_number(of[1].rstrip(",")) if of else None
        if (whole and whole[1] and is_round(whole[0])) or (is_round(v) and not personal):
            continue
        out.append((a, b))
    return out


def lan(line: str, m: re.Match) -> bool:
    """A LAN or tailnet host name, not an attribute (`threading.local()`)."""
    return m[0].lower().endswith(LAN) and not re.match(r"\s*[(=]", line[m.end():m.end() + 8])


def spans(line: str, P: dict, code: bool = False, counting: bool = False, kinds=KINDS) -> list[tuple[int, int, str]]:
    """What ``line`` gives away, as (start, end, kind), overlaps resolved to
    the longest match. ``code``: the line is inside a fence; ``counting``:
    its block names AI use."""
    rx = []
    if "tier" in kinds:
        rx += [("tier", re.compile(t)) for t in TIERS] if P.get("tiers") is None else [("tier", re.compile(t, re.I)) for t in P["tiers"]]
    if "private" in kinds:
        names = P.get("private", []) + [t for t in P.get("auto", []) if code or not t.isalpha()]
        rx += [("private", r) for r in PRIVATE]
        rx += [("private", re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", re.I)) for t in names]
    if "host" in kinds:
        rx += [("host", r) for r in HOSTS]
    found = [(m.start(), m.end(), k) for k, r in rx for m in r.finditer(line) if k != "host" or not SAFE_IP.match(m[0])]
    if "private" in kinds:
        found += [(m.start(), m.end(), "private") for m in EMAIL.finditer(line)
                  if not re.search(r"noreply|@example\.(com|org|net)$", m[0], re.I)]
    if "host" in kinds:
        found += [(m.start(), m.end(), "host") for m in HOSTNAME.finditer(line) if lan(line, m)]
    if "usage" in kinds:
        found += [(a, b, "usage") for a, b in usage(line, code, counting)]
    out = []
    for s in sorted(found, key=lambda s: (s[0], s[0] - s[1])):
        if not out or s[0] >= out[-1][1]:
            out.append(s)
    return out


def layout(lines: list[str]) -> list[tuple[bool, bool]]:
    """Each line's (code, counting): code inside a fence; counting where its
    block names AI use. A block is a paragraph or a fenced block, or for a
    fence tagged as source code each line alone: its literals (3600, 1024)
    are not counts unless the line speaks of tokens or calls."""
    out, i = [(False, False)] * len(lines), 0
    while i < len(lines):
        f = FENCE_OPEN.match(lines[i])
        if f:
            j = next((k for k in range(i + 1, len(lines)) if lines[k].lstrip(" \t").startswith(f[1])), len(lines))
            inner, src = lines[i + 1:j], f[2].lower() in SOURCE
            whole = bool(USAGE_NOUN.search("\n".join(inner)))
            out[i:j + 1] = [(True, False)] + [(True, bool(USAGE_NOUN.search(l)) if src else whole) for l in inner] + [(True, False)] * (j < len(lines))
            i = j + 1
        elif lines[i].strip():
            j = i
            while j < len(lines) and lines[j].strip() and not FENCE_OPEN.match(lines[j]):
                j += 1
            out[i:j] = [(False, bool(USAGE_NOUN.search("\n".join(lines[i:j]))))] * (j - i)
            i = j
        else:
            i += 1
    return out


def front(lines: list[str], channel: str) -> tuple[set[int], tuple[int, int] | None]:
    """The leading envelope's line indexes, and the disclosure's first
    parenthesis as offsets into the text (it may wrap), as ``visible``
    finds them."""
    k = next((i for i, l in enumerate(lines) if l.strip()), len(lines))
    k += channel in ("issue", "pr") and k < len(lines) and lines[k].startswith("# ")
    body = [i for i in range(k, len(lines)) if lines[i].strip()]
    if body and lines[body[0]].lstrip().startswith("<!--"):
        body = body[next((n for n, i in enumerate(body) if lines[i].rstrip().endswith("-->")), -1) + 1:]
    skip = set()
    while body and ENVELOPE.match(lines[body[0]].strip()):
        skip.add(body.pop(0))
    if not body or not DISCLOSURE.search(lines[body[0]]):
        return skip, None
    d = e = body[0]
    while e < len(lines) and lines[e].strip():
        e += 1
    m = re.search(r"\([^()]*\)", "\n".join(lines[d:e]))
    base = sum(len(l) + 1 for l in lines[:d])
    return skip, (base + m.start(), base + m.end()) if m else None


def privacy_flags(text: str, channel: str, P: dict) -> list[dict]:
    """Every flagged span in the posted text, title included: {kind, what,
    line, n}, where ``line`` is the sha256 of the whitespace-normalised line
    it stands in and ``n`` its line number in the draft file."""
    lines = text.splitlines()
    skip, model = front(lines, channel)
    flags, at = [], 0
    for k, (line, (code, counting)) in enumerate(zip(lines, layout(lines))):
        found = spans(line, P, code, counting) if k not in skip else []
        h = sha(norm(line).encode()) if found else None
        for a, b, kind in found:
            if (kind == "tier" and model and model[0] <= at + a and at + b <= model[1] and P.get("model") != "family"
                    and not PLAN_WORD.search(line[a:b])):
                continue  # the authoring model the disclosure names by rule
            flags.append({"kind": kind, "what": line[a:b], "line": h, "n": k + 1})
        at += len(line) + 1
    return flags


def scrub(text: str, P: dict, keep: set, code: bool = True) -> str:
    """``text`` with its paths, e-mail, user and host names replaced by
    ``<private>``, except what the user allowed: what a judge receives of a
    claim, whose output can say more than the draft. Numbers stay; the judge
    checks the draft's against them."""
    out = []
    for line in text.split("\n"):
        for a, b, _ in reversed(spans(line, P, code, kinds=("private", "host"))):
            line = line if line[a:b] in keep else line[:a] + "<private>" + line[b:]
        out.append(line)
    return "\n".join(out)


def open_flags(flags: list[dict], L: dict) -> list[dict]:
    """The flags no allow covers: an allow names the span and its line."""
    allowed = {(a["what"], a["line"]) for a in L.get("allows", [])}
    return [f for f in flags if (f["what"], f["line"]) not in allowed]


def check_draft(text: str, L: dict, flags: list[dict]) -> list[tuple[str, bool, str]]:
    """Every symbolic check on a draft, as (name, passed, detail); ``flags``
    from ``privacy_flags``."""
    user, quiet = L["user"], L["rules"].get("no_mentions", False)
    title, body = split_title(text, L["channel"])
    vis = visible(body)
    first, last = (vis[0] if vis else ""), (vis[-1] if vis else "")
    prose = strip_code(body)
    handle = re.search(rf"(?<![\w@]){re.escape(user)}\b", first) if quiet else re.search(rf"@{re.escape(user)}\b", first)
    mentions = sorted({m for m in MENTION.findall(prose) if quiet or m.lower() != f"@{user}".lower()})
    bad_summary = [k + 1 for k, l in enumerate(body.splitlines()) if "</summary>" in l
                   and not (k + 1 < len(body.splitlines()) and not body.splitlines()[k + 1].strip())]
    missing_claims = [c["id"] for c in L["claims"] if norm(c["text"]) not in norm(body)]
    missing_links = [c["id"] for c in L["claims"] if c.get("permalink") and c["permalink"] not in body]
    words = [f"'{m.group(0)}' in \"{l.strip()[:60]}\"" for l in prose.splitlines() for m in FORBIDDEN.finditer(l)]
    cited = [n for n, m in L["marks"].items() if m["mark"] != "unrelated"
             and not re.search(rf"(#{n}\b|/(issues|pull)/{n}\b)", body)]
    held = open_flags(flags, L)
    said = lambda fs: "; ".join(f'L{f["n"]} {f["kind"]} "{f["what"]}"' for f in fs)
    checks = [
        ("title", L["channel"] not in ("issue", "pr") or bool(title), "first line `# Title`" if not title else title),
        ("disclosure first", bool(DISCLOSURE.search(first)), first[:100]),
        ("user handle", bool(handle), f"{user} without @ (no-mentions house rule)" if quiet else f"@{user} on the first line"),
        ("footer last", bool(FOOTER.search(last)), last[:100]),
        ("no other mentions", not mentions, ", ".join(mentions) or "none"),
        ("blank after </summary>", not bad_summary, f"lines {bad_summary}" if bad_summary else "ok"),
        ("claims verbatim", not missing_claims, f"claims {missing_claims} not in the draft" if missing_claims else "all present"),
        ("permalinks", not missing_links, f"claims {missing_links}: permalink not in the draft" if missing_links else "all present"),
        ("no hedging words", not words, "; ".join(words[:5]) or "none"),
        ("related line", bool(RELATED.search(prose)), "a `Related:` line, or `Related: none found by <command>`"),
        ("related cited", not cited, f"marked related/duplicate but not cited: {cited}" if cited else "ok"),
        ("private details", not held, f"{said(held)}: a ratio, a per-unit figure or no name instead, or the user's yes to each: "
         "`approve --by @HANDLE --allow SPAN --why ...`" if held else f"allowed by the user: {said(flags)}" if flags else "none"),
    ]
    if L.get("list"):
        tags = sorted({m.group(0) for m in HTML_TAG.finditer(prose)})
        checks.append(("plain text", not tags, ", ".join(tags[:5]) or "no HTML"))
    return checks


def contract_lines(body: str) -> list[str]:
    """The lines the skill requires in every draft and checks symbolically:
    the disclosure (first visible line), any `Related:` line, the footer
    (last visible line) and an `**Environment.**` paragraph. Judges are told
    not to judge them."""
    vis = visible(body)
    out = ([vis[0]] if vis else []) + [l.strip() for l in body.splitlines() if RELATED.match(l)]
    out += [p.strip() for p in re.split(r"\n\s*\n", body) if re.match(r"\s*\*\*Environment\b", p)]
    out += [vis[-1]] if len(vis) > 1 else []
    return list(dict.fromkeys(out))


def sentences(body: str, L: dict) -> list[str]:
    """The draft's claim-bearing sentences: prose only, without headings,
    quotes, tags and the contract lines (checked symbolically). Inline code
    stays: "`f(x)` returns 1" is a claim a grep or a run can support."""
    skip = {l for c in contract_lines(body) for l in c.splitlines()}
    keep = []
    for line in strip_fences(body).splitlines():
        s = line.strip()
        if not s or s in skip or s.startswith(("#", ">", "|")):
            continue
        keep.append(HTML_TAG.sub("", s))
    parts = re.split(r"(?<=[.!?])\s+", " ".join(keep))
    return [p.strip() for p in parts if len(p.split()) >= 3]


def ledger_entry(c: dict) -> dict:
    """A claim as any judge sees it: the posted sentence, the command line,
    its capped output and the permalink, after ``scrub``. Nothing else
    leaves the machine."""
    return {"id": c["id"], "sentence": c["text"], "command": c["cmd"], "output": c["output"][:4000],
            "permalink": c.get("permalink") or ""}


def decision_requests(sentence: str, claims: list[dict]) -> tuple[dict, Callable[[dict], dict]]:
    """The only builder of what a hosted decision model receives. Returns the
    Choice question (state = the sentence) and a function building the Noul
    question for the chosen entry (state = sentence plus that ledger entry)."""
    entries = [ledger_entry(c) for c in claims]
    crit = {f"c{e['id']}": f"{e['sentence']} (command: {e['command']})" for e in entries}
    crit["none"] = "No entry supports this sentence."
    choice = {"state": sentence, "question": {"type": "choice", "criteria": crit,
              "instructions": "Which claim-ledger entry supports this sentence from a bug report?"}}

    def noul(entry_id: str) -> dict:
        e = next(e for e in entries if f"c{e['id']}" == entry_id)
        state = json.dumps({"sentence": sentence, "command": e["command"], "output": e["output"], "permalink": e["permalink"]})
        return {"state": state, "question": {"type": "noul", "instructions": "Does this command output establish this sentence?"}}
    return choice, noul


def unparseable(why: str) -> list[dict]:
    return [{"quote": "judge output unparseable", "kind": "other", "blocking": True, "why": why[:300]}]


def parse_verdict(raw: str) -> list[dict]:
    """Findings from a judge's output: the first balanced JSON object that
    carries a ``findings`` list, found with the JSON decoder itself (so
    braces inside strings and prose around the object do not confuse it).
    Fails closed: anything that is not a well-formed verdict comes back as
    one blocking finding, never as a pass."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(raw, i)
        except ValueError:
            continue
        found = obj.get("findings") if isinstance(obj, dict) else None
        if not isinstance(found, list):
            continue
        if not all(isinstance(f, dict) and isinstance(f.get("quote"), str) and isinstance(f.get("blocking"), bool) for f in found):
            return unparseable(f"malformed finding in {json.dumps(found)[:200]}")
        kinds = [f.get("kind") if f.get("kind") in FINDING_KINDS else "other" for f in found]
        return [f | {"kind": k, "blocking": f["blocking"] or k == "private"} for f, k in zip(found, kinds)]
    return unparseable(f"no {{\"findings\": [...]}} object in: {raw.strip()[:200]!r}")


# --------------------------------------------------------------------------
# Imperative shell: ledger I/O, gh, subprocesses.
# --------------------------------------------------------------------------

class Refused(Exception):
    """A transition the machine will not take; the message says why."""


def gh(*args: str, token: str | None = None, stdin: str | None = None) -> tuple[int, str, str]:
    """Run gh with GH_UPSTREAM_SCRIPT=1 (the hook's marker) and, for a post,
    a per-command GH_TOKEN; the token is never printed or logged."""
    env = dict(os.environ, GH_UPSTREAM_SCRIPT="1")
    if token:
        env["GH_TOKEN"] = token
    try:
        p = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, env=env, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)
    return p.returncode, p.stdout, p.stderr


def shell(cmd: str, cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return 124, "[timed out after 600 s]"
    return p.returncode, cap(p.stdout + p.stderr)


def masked(s: str, masks: list[str]) -> str:
    for m in masks:
        s = re.sub(m, "<masked>", s)
    return s


class Doc(str):
    """A draft's sha256 that also carries the draft's text: guards compare it
    as a plain sha, and the approval guard reads ``.text`` for its diff."""
    text = ""


class Work:
    """The work directory: STATE.json plus the files the steps produce."""

    def __init__(self, path: str):
        self.dir = Path(path).resolve()
        self.state = self.dir / "STATE.json"

    def load(self) -> dict:
        if not self.state.exists():
            raise Refused(f"no {self.state}: run `init OWNER/REPO --channel ... --user @HANDLE` first")
        return json.loads(self.state.read_text())

    def draft_sha(self, L: dict) -> "Doc | None":
        p = (L.get("draft") or {}).get("path")
        if not (p and Path(p).exists()):
            return None
        raw = Path(p).read_bytes()
        d = Doc(sha(raw))
        d.text = raw.decode(errors="replace")
        return d

    def save(self, L: dict) -> tuple[str, list[str]]:
        L["state"], missing = derive(L, self.draft_sha(L))
        self.state.write_text(json.dumps(L, indent=1) + "\n")
        return L["state"], missing

    def log(self, L: dict, line: str):
        L["log"].append(f"{now()} {line}")


def require(L: dict, w: Work, at_least: str, exactly: bool = False):
    """Refuse a command before its step is reached, at a stop state, and
    after posting."""
    s, missing = derive(L, w.draft_sha(L))
    if s not in ORDER or s == "posted":
        raise Refused(f"the flow has ended at `{s}`: {STATES[s][0]}")
    if ORDER.index(s) < ORDER.index(at_least) or (exactly and s != at_least):
        raise Refused(f"state is `{s}`, this needs `{at_least}`. Missing: " + "; ".join(missing or ["-"]))


def allowed(L: dict) -> list[dict]:
    """The allows that cover a flag on the last checked draft."""
    cur = {(f["what"], f["line"]) for f in (L.get("draft") or {}).get("flags", [])}
    return [x for x in L.get("allows", []) if (x["what"], x["line"]) in cur]


def status_text(L: dict, w: Work) -> str:
    s, missing = derive(L, w.draft_sha(L))
    lines = [f"{L['repo']} · {L['channel']}{' #' + str(L['thread']) if L.get('thread') else ''} · state: {s} ({STATES[s][0]})"]
    nxt = next((e for e in EDGES if e.src == s and e.kind == "go"), None)
    if missing and nxt:
        lines.append(f"to reach `{nxt.dst}` ({nxt.label}), missing:")
        lines += [f"  - {m}" for m in missing]
    j = L.get("judge") or {}
    if j.get("status") == "not judged":
        lines.append(f"judge: not judged ({j.get('reason')})")
    if L["rules"].get("no_mentions"):
        lines.append(f"deviation: the house rules forbid mentions, so the handle is written without @ ({L['user']})")
    lines += [f'allowed by {x["by"]}: {x["kind"]} "{x["what"]}" ({x["why"]})' for x in allowed(L)]
    return "\n".join(lines)


# --- commands --------------------------------------------------------------

def cmd_init(a, w: Work):
    if w.state.exists():
        raise Refused(f"{w.state} exists; one work directory per post (use --workdir)")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        raise Refused("repo must be OWNER/REPO")
    if a.channel == "comment" and not a.thread:
        raise Refused("--channel comment needs --thread N")
    if a.list and a.channel != "issue":
        raise Refused("--list is a report to a mailing list: use --channel issue")
    w.dir.mkdir(parents=True, exist_ok=True)
    L = {"repo": a.repo, "channel": a.channel, "thread": a.thread, "on": a.on, "list": a.list,
         "user": a.user.lstrip("@"), "rules": {}, "searches": [], "hits": {}, "marks": {},
         "claims": [], "env": None, "draft": None, "judge": None, "approval": None, "post": None, "log": []}
    w.log(L, f"init {a.repo} channel={a.channel}")
    return L


def policy_hits(name: str, text: str) -> list[str]:
    return [f"{name}:{k}: {l.strip()[:160]}" for k, l in enumerate(text.splitlines(), 1)
            if any(t.search(l) for t in POLICY_TERMS)]


def fetch_local(clone: Path) -> dict[str, str]:
    found = {}
    for d in ("", "docs", ".github"):
        base = clone / d
        if base.is_dir():
            found |= {str(p.relative_to(clone)): p.read_text(errors="replace") for p in base.iterdir()
                      if p.is_file() and POLICY_NAME.match(p.name)}
    gh_dir = clone / ".github"
    if gh_dir.is_dir():
        for p in gh_dir.rglob("*"):
            rel = p.relative_to(gh_dir)
            if p.is_file() and (rel.parts[0] in ("ISSUE_TEMPLATE", "DISCUSSION_TEMPLATE", "PULL_REQUEST_TEMPLATE")
                                or p.name.lower().startswith("pull_request_template")):
                found[str(p.relative_to(clone))] = p.read_text(errors="replace")
    return found


def fetch_api(repo: str) -> dict[str, str]:
    rc, out, err = gh("api", f"repos/{repo}/contents/")
    if rc:
        raise Refused(f"`gh api repos/{repo}/contents/` failed ({(out + err).strip()[:120]}); rerun with --local CLONE")
    listing = json.loads(out)
    for d in ("docs", ".github", ".github/ISSUE_TEMPLATE", ".github/PULL_REQUEST_TEMPLATE", ".github/DISCUSSION_TEMPLATE"):
        rc, more, _ = gh("api", f"repos/{repo}/contents/{d}")
        listing += json.loads(more) if rc == 0 and more.lstrip().startswith("[") else []
    want = [f["path"] for f in listing if f.get("type") == "file" and (
        (POLICY_NAME.match(f["name"]) and f["path"].count("/") <= 1)
        or f["path"].startswith((".github/ISSUE_TEMPLATE/", ".github/PULL_REQUEST_TEMPLATE", ".github/DISCUSSION_TEMPLATE/"))
        or f["name"].lower().startswith("pull_request_template"))]
    found = {}
    for p in want:
        rc, raw, _ = gh("api", f"repos/{repo}/contents/{p}", "-H", "Accept: application/vnd.github.raw")
        if rc == 0:
            found[p] = raw
    return found


def cmd_rules(a, w: Work):
    L = w.load()
    require(L, w, "rules")
    if a.action == "classify":
        if not L["rules"].get("fetched"):
            raise Refused("fetch the rules first: `rules` (or `rules --local CLONE`)")
        L["rules"] |= {"outcome": a.outcome, "form": a.form, "note": a.note or "",
                       "security": a.security, "no_mentions": a.no_mentions, "classified": now()}
        w.log(L, f"rules classify {a.outcome} form={a.form!r} security={a.security} no_mentions={a.no_mentions}")
        return L
    if a.local and not Path(a.local, ".git").exists():
        raise Refused(f"--local {a.local} is not a git clone")
    files = fetch_local(Path(a.local)) if a.local else fetch_api(L["repo"])
    rdir = w.dir / "rules"
    rdir.mkdir(exist_ok=True)
    hits = []
    for name, text in sorted(files.items()):
        (rdir / name.replace("/", "__")).write_text(text)
        hits += policy_hits(name, text)
    src = {"source": "local", "clone": str(Path(a.local).resolve())} if a.local else {"source": "api"}
    if a.local:
        rc, remote = shell("git remote get-url origin; git rev-parse HEAD", a.local)
        src["remote"] = remote.strip()
    rc, out, err = gh("api", f"repos/{L['repo']}")
    if rc:
        rc, out, err = gh("repo", "view", L["repo"], "--json", "name,isArchived,pushedAt,hasIssuesEnabled")
    live = {"repo": (out or err).strip()[:2000]}
    rc, out, err = gh("pr", "list", "-R", L["repo"], "--state", "merged", "--limit", "5", "--json", "number,title,mergedAt")
    live["merged_prs"] = (out or err).strip()[:2000]
    if L.get("thread"):
        rc, out, err = gh(L["on"], "view", str(L["thread"]), "-R", L["repo"], "--comments")
        (rdir / "thread.txt").write_text(out + err)
        hits += policy_hits("thread", out)
    L["rules"] |= {"fetched": now(), "files": sorted(files), "absent": not files, "hits": hits, "liveness": live} | src
    w.log(L, f"rules fetched {len(files)} file(s) from {src['source']}, {len(hits)} hit(s)")
    print(f"policy files ({src['source']}): {', '.join(sorted(files)) or 'none (recorded absent)'}")
    print("policy hits:" if hits else "policy hits: none", *hits, sep="\n  ")
    print(f"liveness: repo {live['repo'][:200]}\n  merged PRs: {live['merged_prs'][:300]}")
    print("next: read the files in rules/, then `rules classify --outcome "
          + "|".join(OUTCOMES) + " --form \"...\" [--security] [--no-mentions]`")
    return L


def cmd_search(a, w: Work):
    L = w.load()
    require(L, w, "search")
    if a.action == "mark":
        if a.number not in L["hits"]:
            raise Refused(f"#{a.number} is not among the search hits {sorted(L['hits'], key=int)}")
        L["marks"][a.number] = {"mark": a.mark, "why": a.why}
        w.log(L, f"search mark #{a.number} {a.mark}: {a.why}")
        return L
    raw = json.loads((w.dir / "related.json").read_text()) if (w.dir / "related.json").exists() else []
    runs = []
    if a.keywords:
        for kind, sub, fields in (("issues", "issue", "number,title,state,stateReason,url"), ("prs", "pr", "number,title,state,url")):
            runs.append((kind, ["gh", sub, "list", "-R", L["repo"], "--state", "all", "--search", a.keywords, "--json", fields]))
    if a.archive_cmd:
        runs.append(("archive", a.archive_cmd))
    if not runs:
        raise Refused("give --keywords \"...\" (issues and PRs) and, for a list, --archive-cmd \"...\"")
    for kind, argv in runs:
        if kind == "archive":
            rc, out = shell(argv, os.getcwd())
            line, err = argv, ""
        else:
            rc, out, err = gh(*argv[1:])
            line = shlex.join(argv)
        L["searches"].append({"kind": kind, "cmd": line, "exit": rc, "at": now()})
        raw.append({"kind": kind, "cmd": line, "exit": rc, "stdout": out, "stderr": err})
        w.log(L, f"search {kind}: {line} -> exit {rc}")
        if kind != "archive" and rc == 0 and out.strip():
            for h in json.loads(out):
                L["hits"][str(h["number"])] = {**h, "kind": kind}
        print(f"$ {line}\n  exit {rc}" + (f": {err.strip()[:200]}" if rc else ""))
    (w.dir / "related.json").write_text(json.dumps(raw, indent=1) + "\n")
    for n, h in sorted(L["hits"].items(), key=lambda x: int(x[0])):
        mark = L["marks"].get(n, {}).get("mark", "UNMARKED")
        print(f"  #{n} [{h.get('state')}{'/' + h['stateReason'] if h.get('stateReason') else ''}] {h.get('title')} -> {mark}")
    return L


def cmd_claim(a, w: Work):
    L = w.load()
    if a.action == "rm":
        require(L, w, "evidence")
        if not any(c["id"] == a.n for c in L["claims"]):
            raise Refused(f"no claim {a.n}; `claim list` shows the ids")
        L["claims"] = [c for c in L["claims"] if c["id"] != a.n]
        w.log(L, f"claim {a.n} removed")
        return L
    if a.action == "list":
        for c in L["claims"]:
            out = "\n".join("    > " + l for l in c["output"].splitlines()[:8])
            print(f"[{c['id']}] {c['text']}\n    $ {c['cmd']}  (exit {c['exit']}, cwd {c['cwd']})\n{out}\n"
                  f"    {c.get('permalink') or 'no permalink: ' + c.get('no_permalink', '')}")
        return None
    require(L, w, "evidence")
    if not (a.permalink or a.no_permalink):
        raise Refused("every claim needs --permalink URL or --no-permalink \"why none applies\"")
    pinned = re.search(r"/(blob|tree)/([^/]+)/", a.permalink or "")
    if pinned and not re.fullmatch(r"[0-9a-f]{40}", pinned.group(2)):
        raise Refused(f"permalink is not pinned to a commit: `{pinned.group(2)}` is not a 40-char sha (git rev-parse HEAD)")
    rc, out = shell(a.cmd, os.getcwd())
    next_id = max((c["id"] for c in L["claims"]), default=0) + 1  # ids stay stable across `claim rm`
    c = {"id": next_id, "text": a.text, "cmd": a.cmd, "cwd": os.getcwd(), "exit": rc, "output": out,
         "permalink": a.permalink, "no_permalink": a.no_permalink, "masks": a.mask or [], "at": now()}
    L["claims"].append(c)
    w.log(L, f"claim {c['id']} added: exit {rc}")
    print(f"claim {c['id']}: $ {a.cmd}\nexit {rc}\n{out[:2000]}")
    return L


def cmd_env(a, w: Work):
    L = w.load()
    require(L, w, "evidence")
    if a.text:
        L["env"] = {"text": a.text, "source": "given"}
    else:
        probes = ["uname -srm", "sw_vers 2>/dev/null", "sysctl -n hw.model machdep.cpu.brand_string hw.memsize 2>/dev/null"]
        L["env"] = {"text": "\n".join(shell(p, os.getcwd())[1].strip() for p in probes).strip(), "source": "detected"}
    w.log(L, "env recorded")
    print(L["env"]["text"])
    return L


def privacy_config(L: dict) -> dict:
    """The privacy terms in force, read at the edge on every use and never
    stored (STATE.json would otherwise list what must not be said): the
    config file (``UPSTREAM_CONFIG``, else ``~/.config/upstream/config.json``)
    with "private" terms, "tiers" regexes that replace the defaults and
    "model": "full" (default) or "family" for the disclosure line; plus, as
    "auto", the login and host name, which pasted output gives away."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = Path(os.environ.get("UPSTREAM_CONFIG") or base / "upstream" / "config.json")
    try:
        cfg = json.loads(path.read_text()) if path.is_file() else {}
        if not isinstance(cfg, dict) or set(cfg) - {"private", "tiers", "model"} or cfg.get("model", "full") not in ("full", "family"):
            raise ValueError(f"got {json.dumps(cfg)[:120]}")
        for k in ("private", "tiers"):
            if not (isinstance(cfg.get(k, []), list) and all(isinstance(t, str) for t in cfg.get(k, []))):
                raise ValueError(f"{k} is not a list of strings")
        [re.compile(t) for t in cfg.get("tiers", [])]
    except (ValueError, re.error) as e:
        raise Refused(f"{path}: keys private (terms), tiers (regexes), model (full|family); {e}")
    login = os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()
    host = (os.environ.get("HOSTNAME") or socket.gethostname()).split(".")[0]
    auto = [t for t in (login, host) if len(t) >= 3 and t.lower() not in COMMON_NAMES | {L["user"].lower()}]
    return {"private": cfg.get("private", []), "auto": auto, "tiers": cfg.get("tiers"), "model": cfg.get("model", "full")}


def render(body: str, repo: str, w: Work) -> dict:
    """GitHub's own rendering, to count what a reader sees; any failure
    (no gh, not GitHub, a 404) is a note, not a block."""
    rc, out, err = gh("api", "markdown", "--input", "-", stdin=json.dumps({"text": body, "mode": "gfm", "context": repo}))
    if rc or not out.lstrip().startswith("<"):
        return {"skipped": f"gh api markdown exit {rc}: {(err or out).strip()[:160]}"}
    (w.dir / "preview.html").write_text(out)
    return {"details": out.count("<details"), "code_blocks": out.count("<pre"),
            "mentions": out.count('class="user-mention"'), "html": str(w.dir / "preview.html")}


def cmd_draft(a, w: Work):
    L = w.load()
    require(L, w, "draft")
    path = Path(a.file).resolve()
    text = path.read_text()
    L.pop("privacy", None)  # never stored: the terms are themselves private
    flags = privacy_flags(text, L["channel"], privacy_config(L))
    checks = check_draft(text, L, flags)
    title, body = split_title(text, L["channel"])
    (w.dir / "body.md").write_text(body)
    for c in L["claims"]:
        rc, out = shell(c["cmd"], c["cwd"])
        same = rc == c["exit"] and masked(out, c["masks"]) == masked(c["output"], c["masks"])
        diff = "" if same else "".join(difflib.unified_diff(
            c["output"].splitlines(True), out.splitlines(True), f"claim {c['id']} recorded (exit {c['exit']})", f"now (exit {rc})"))
        checks.append((f"claim {c['id']} re-run", same, "same output" if same else diff[:1500]))
    rendered = render(body, L["repo"], w)
    failures = [n for n, ok, _ in checks if not ok]
    L["draft"] = {"path": str(path), "sha": sha(text.encode()), "inputs": inputs_sha(L), "title": title,
                  "passed": not failures, "failures": failures, "flags": flags,
                  "render": rendered, "at": now()}
    w.log(L, f"draft check {'passed' if not failures else 'FAILED: ' + ', '.join(failures)}")
    for n, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {n}: {detail}")
    print("render: " + (f"skipped ({rendered['skipped']})" if "skipped" in rendered else
                        f"{rendered['details']} <details>, {rendered['code_blocks']} code blocks, {rendered['mentions']} mentions"))
    return L


def decide(backend: str, req: dict) -> dict:
    """One decision-model call; ``req`` comes from ``decision_requests``."""
    q = req["question"]
    if backend == "von":
        import von  # lazy: only when asked for
        if q["type"] == "choice":
            ans = von.decide(req["state"], choices=q["criteria"], instructions=q["instructions"])
            return {"choice": ans.choice, "probabilities": dict(ans.probabilities)}
        return {"noul": float(von.judge(req["state"], instructions=q["instructions"]))}
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("SANDBOX_OPENROUTER_API_KEY")
    body = json.dumps({"model": JEV_MODEL, "state": req["state"], "questions": {"q": q}}).encode()
    http = urllib.request.Request(JEV_URL, data=body, method="POST",
                                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(http, timeout=60) as r:
        return json.loads(r.read())["answers"]["q"]


def pick_backend(choice: str) -> tuple[str | None, str]:
    has_key = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("SANDBOX_OPENROUTER_API_KEY"))
    if choice == "none":
        return None, "decision model disabled by --judge-model none"
    if choice in ("jev", "auto") and has_key:
        return "jev", "Jev via OpenRouter"
    if choice == "jev":
        return None, "no OPENROUTER_API_KEY for Jev"
    try:
        import von  # noqa: F401
        return "von", "local Von"
    except ImportError:
        return None, "no decision model: no OpenRouter key and von is not importable"


def llm_judge(cmd: str, request: dict) -> list[dict]:
    """Run the adversarial judge: ``cmd`` gets the JSON request on stdin and
    prints the verdict. A crash, a timeout or a non-zero exit is a blocking
    finding, like an unparseable verdict."""
    try:
        p = subprocess.run(cmd, shell=True, input=json.dumps(request), capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return unparseable("judge command timed out after 900 s")
    if p.returncode:
        return unparseable(f"judge command exit {p.returncode}: {p.stderr.strip()[-200:]}")
    return parse_verdict(p.stdout)


def cmd_judge(a, w: Work):
    L = w.load()
    require(L, w, "judged")
    jlog = w.dir / "judge.log"
    h = w.draft_sha(L)

    def record(entry: dict):
        with jlog.open("a") as f:
            f.write(json.dumps({"at": now(), "draft": h, **entry}) + "\n")

    if a.action == "override":
        j = L["judge"]
        f = next((f for f in j.get("findings", []) if f["n"] == a.n and f.get("blocking")), None)
        if not f or j.get("sha") != h:
            raise Refused(f"no blocking finding {a.n} on the current draft")
        j.setdefault("overrides", []).append({"n": a.n, "reason": a.reason, "at": now()})
        record({"kind": "override", "n": a.n, "quote": f["quote"], "reason": a.reason})
        w.log(L, f"judge override {a.n}: {a.reason}")
        return L
    body = (w.dir / "body.md").read_text()
    sents = sentences(body, L)
    P, keep = privacy_config(L), {x["what"] for x in allowed(L)}
    claims = [c | {k: scrub(c[k], P, keep, code=k != "text") for k in ("text", "cmd", "output")} for c in L["claims"]]
    backend, why = pick_backend(a.judge_model)
    decisions, flagged = [], []
    for s in sents if backend else []:
        choice, noul = decision_requests(s, claims)
        ans = decide(backend, choice)
        entry, p = ans.get("choice", "none"), None
        if entry != "none":
            p = float(decide(backend, noul(entry)).get("noul", 0))
        ok = entry != "none" and p >= a.noul_threshold
        decisions.append({"sentence": s, "entry": entry, "p": p, "supported": ok})
        record({"kind": "decision", "model": backend, "sentence": s, "entry": entry, "p": p, "supported": ok})
        flagged += [] if ok else [s]
    codex = HERE / "judges" / "codex.sh"
    judge_cmd = a.judge_cmd or (shlex.quote(str(codex)) if shutil.which("codex") else None)
    j = {"sha": h, "inputs": inputs_sha(L), "decision_model": backend or f"none ({why})", "decisions": decisions,
         "flagged": flagged, "findings": [], "overrides": [], "at": now()}
    if judge_cmd in (None, "none"):
        llm_why = "disabled by --judge-cmd none" if judge_cmd == "none" else "no --judge-cmd and codex is not on PATH"
        j["llm"] = f"none ({llm_why})"
        j["status"] = "judged" if backend else "not judged"
        j["reason"] = llm_why if backend else f"{why}; {llm_why}"
    else:
        request = {"instructions": JUDGE_PROMPT.replace("OWNER/REPO", L["repo"]), "draft": body,
                   "contract_lines": contract_lines(body), "ledger": [ledger_entry(c) for c in claims], "flagged": flagged,
                   "allowed": sorted({x["what"] for x in allowed(L)})}
        j["llm"] = judge_cmd
        j["findings"] = [{"n": k, **f} for k, f in enumerate(llm_judge(judge_cmd, request), 1)]
        j["status"] = "judged"
        for f in j["findings"]:
            record({"kind": "llm", "judge": j["llm"], **f})
    L["judge"] = j
    w.log(L, f"judge {j['status']}: {len(flagged)} flagged, {sum(f['blocking'] for f in j['findings'])} blocking")
    print(f"decision model: {j['decision_model']}; {len(sents)} sentence(s), {len(flagged)} flagged")
    for s in flagged:
        print(f"  flagged: {s[:120]}")
    print(f"llm judge: {j['llm']} -> {j['status']}" + (f" ({j.get('reason')})" if j.get("reason") else ""))
    for f in j["findings"]:
        print(f"  [{f['n']}] {'BLOCKING' if f['blocking'] else 'note'} {f['kind']}: \"{f['quote'][:100]}\" -- {f.get('why', '')}")
    return L


def cmd_approve(a, w: Work):
    """Record the user's yes to one text. Without --for: the checked, judged
    draft, and only in state `approval`. With --for FILE: the text the user
    approved in chat, which may come before the checks; `post` then accepts
    the checked draft only if it adds nothing but the disclosure, Related and
    footer lines to that text (``extension_diff``)."""
    L = w.load()
    if a.by.lstrip("@").lower() != L["user"].lower():
        raise Refused(f"only the user @{L['user']} approves; got {a.by}")
    if a.allow:  # the user's yes to flagged details, each bound to the line it stands in
        require(L, w, "draft")
        d = L.get("draft") or {}
        if a.for_file or d.get("sha") != w.draft_sha(L):
            raise Refused("--allow takes a span the last `draft check` flagged on this draft: run `draft check` first")
        flagged = d.get("flags", [])
        if unknown := [s for s in a.allow if not any(f["what"] == s for f in flagged)]:
            raise Refused(f"not flagged: {', '.join(map(json.dumps, unknown))}; flagged: "
                          + (", ".join(sorted({json.dumps(f["what"]) for f in flagged})) or "nothing"))
        hits = list({(f["what"], f["line"]): f for f in flagged if f["what"] in a.allow}.values())
        keys = {(f["what"], f["line"]) for f in hits}
        L["allows"] = [x for x in L.get("allows", []) if (x["what"], x["line"]) not in keys] + [
            {k: f[k] for k in ("kind", "what", "line")} | {"why": a.why, "by": a.by, "at": now()} for f in hits]
        w.log(L, f"allowed by {a.by} on {len(hits)} line(s): {', '.join(map(json.dumps, a.allow))}: {a.why}")
        return L
    if a.for_file:
        require(L, w, "rules")
        path = Path(a.for_file).resolve()
        raw = path.read_bytes()
        L["approval"] = {"by": a.by, "file": str(path), "sha": sha(raw), "text": raw.decode(errors="replace"), "at": now()}
    else:
        require(L, w, "approval", exactly=True)
        d = w.draft_sha(L)
        L["approval"] = {"by": a.by, "file": L["draft"]["path"], "sha": str(d), "text": d.text, "at": now()}
    w.log(L, f"approved by {a.by}: {L['approval']['file']} sha {L['approval']['sha'][:12]}")
    return L


def auth_ok(user: str) -> bool:
    """`gh auth status` still has the user's account active."""
    rc, out, err = gh("auth", "status")
    text = (out + err).splitlines()
    k = next((i for i, l in enumerate(text) if re.search(rf"account {re.escape(user)}\b", l)), None)
    return k is not None and any("Active account: true" in l for l in text[k + 1:k + 3])


def machine_token(account: str) -> str:
    """The machine account's token, for one command's environment only:
    never `gh auth switch`, never printed."""
    rc, token, _ = gh("auth", "token", "--user", account)
    if rc or not token.strip():
        raise Refused(f"no token for the machine account {account}: `gh auth login` it first")
    return token.strip()


def cmd_post(a, w: Work):
    L = w.load()
    require(L, w, "approved", exactly=True)
    if L.get("list"):
        raise Refused(f"a mailing-list report is sent by the human to {L['list']}; this script posts only to GitHub")
    token = machine_token(a.account)
    body, repo, d = str(w.dir / "body.md"), L["repo"], L["draft"]
    argv = {"issue": ["issue", "create", "-R", repo, "--title", d.get("title") or "", "--body-file", body],
            "pr": ["pr", "create", "-R", repo, "--title", d.get("title") or "", "--body-file", body] + (["--head", a.head] if a.head else []),
            "comment": [L["on"], "comment", str(L["thread"]), "-R", repo, "--body-file", body]}[L["channel"]]
    rc, out, err = gh(*argv, token=token)
    out, err = out.replace(token, "<token>"), err.replace(token, "<token>")
    url = next((l.strip() for l in reversed(out.splitlines()) if l.strip().startswith("https://")), None)
    if rc or not url:
        raise Refused(f"gh {' '.join(argv[:2])} failed (exit {rc}): {err.strip()[:300]}")
    L["post"] = {"url": url, "account": a.account, "at": now(), "draft": d["sha"]}  # recorded before any check
    w.save(L)
    num = re.search(r"/(issues|pull)/(\d+)", url).group(2)
    cid = re.search(r"#issuecomment-(\d+)", url)
    rc, out, _ = (gh("api", f"repos/{repo}/issues/comments/{cid.group(1)}") if cid else
                  gh("issue" if L["channel"] == "issue" else "pr", "view", num, "-R", repo, "--json", "author"))
    try:  # parsed here rather than with --jq, so any gh (or shim) that answers JSON will do
        got = json.loads(out)
        author = (got.get("user") or got.get("author"))["login"]
    except (ValueError, TypeError, KeyError):
        author = f"unverified (gh exit {rc}: {out.strip()[:80]})"
    L["post"] |= {"author": author, "verified": author == a.account, "user_still_active": auth_ok(L["user"])}
    with (w.dir / "RECORD.md").open("a") as f:
        f.write(f"- {now()} {L['channel']} {url} posted as {a.account}; author {author}; "
                f"draft sha256 {d['sha'][:12]}; approved by {L['approval']['by']}; "
                + "".join(f'allowed "{x["what"]}"; ' for x in allowed(L)) +
                f"gh active account still {L['user']}: {L['post']['user_still_active']}\n")
    w.log(L, f"posted {url} author={author}")
    print(url)
    if not (L["post"]["verified"] and L["post"]["user_still_active"]):
        print(f"WARNING: author {author!r} (want {a.account}), user active {L['post']['user_still_active']}: check by hand",
              file=sys.stderr)
        w.save(L)
        sys.exit(3)
    return L


def git(*args: str, cwd: Path, env: dict | None = None) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=600)
    return p.returncode, (p.stdout + p.stderr).strip()


def cmd_fork(a, w: Work):
    """Rung 3 of step 8: the patch series, one commit per patch on the
    upstream tag, pushed to a fork under the machine account. Nothing leaves
    the machine until every patch has applied; the fork's URL goes to
    RECORD.md for the draft to cite (which sends the draft back through
    check, judge and approval)."""
    L = w.load()
    s, missing = derive(L, w.draft_sha(L))
    if s not in ("approved", "posted"):
        raise Refused(f"state is `{s}`: a fork follows the user's go (`approved`). Missing: " + "; ".join(missing or ["-"]))
    patches = sorted(Path(a.patches).glob("*.patch")) if Path(a.patches).is_dir() else []
    if not patches:
        raise Refused(f"no *.patch files in {a.patches} (git format-patch output, or -p1 patches with a prose header)")
    signed = [p.name for p in patches if re.search(r"^Signed-off-by:", p.read_text(errors="replace"), re.M)]
    if signed or any(t.lower().startswith("signed-off-by") for t in a.trailer or []):
        raise Refused(f"Signed-off-by in {signed or 'the trailers'}: only the human certifies the DCO; remove it")
    if not any(t.lower().startswith("assisted-by:") for t in a.trailer or []):
        raise Refused("give at least --trailer \"Assisted-By: <model slug>\" (and Harness, Session-Id as you have them)")
    name = L["repo"].split("/")[1]
    upstream = a.upstream_url or f"https://github.com/{L['repo']}.git"
    push = a.push_url or f"https://github.com/{a.account}/{name}.git"
    clone = w.dir / "fork"
    shutil.rmtree(clone, ignore_errors=True)
    rc, out = git("clone", "--quiet", upstream, str(clone), cwd=w.dir)
    if rc:
        raise Refused(f"git clone {upstream} failed: {out[:200]}")
    rc, out = git("checkout", "--quiet", "-b", a.branch, a.base_tag, cwd=clone)
    if rc:
        raise Refused(f"no tag {a.base_tag} upstream: {out[:200]}")
    extra = [x for t in a.trailer for x in ("--trailer", t)]
    for p in patches:  # the dry run: apply all locally before anything is published
        text = p.read_text(errors="replace")
        if text.startswith("From "):  # git format-patch: the mail carries the message
            rc, out = git("am", "--quiet", str(p.resolve()), cwd=clone)
            git("am", "--abort", cwd=clone) if rc else None
            commit = ["commit", "--amend", "--quiet", "--no-edit"]
        else:  # a plain -p1 patch: its header, up to the first diff line, is the message
            rc, out = git("apply", "--index", str(p.resolve()), cwd=clone)
            msg = re.split(r"^(?:diff --git |--- |Index: )", text, maxsplit=1, flags=re.M)[0].strip()
            commit = ["commit", "--quiet", "-m", msg or p.stem]
        if rc:
            raise Refused(f"{p.name} does not apply on {a.base_tag} after {patches.index(p)} patch(es); nothing was pushed:\n{out[:600]}")
        rc, out = git(*commit, f"--author={a.author}", *extra, cwd=clone)
        if rc:
            raise Refused(f"committing {p.name} failed: {out[:200]}")
    head = git("rev-parse", "HEAD", cwd=clone)[1]
    token = machine_token(a.account)
    rc, out, err = gh("repo", "fork", L["repo"], "--clone=false", token=token)
    if rc:
        raise Refused(f"gh repo fork failed (exit {rc}): {(out + err).replace(token, '<token>').strip()[:300]}")
    env = dict(os.environ, GH_TOKEN=token, GH_UPSTREAM_SCRIPT="1", GIT_TERMINAL_PROMPT="0")
    rc, out = git("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                  "push", "--quiet", push, f"HEAD:refs/heads/{a.branch}", cwd=clone, env=env)
    if rc:
        raise Refused(f"push to {push} failed: {out.replace(token, '<token>')[:300]}")
    rc, out, _ = gh("api", f"repos/{a.account}/{name}/branches/{a.branch}")
    how, remote = "gh api", (json.loads(out).get("commit", {}).get("sha", "") if rc == 0 and out.lstrip().startswith("{") else "")
    if not remote:  # a fork too fresh for the API, or no API at all: ask git itself
        rc, remote = git("ls-remote", push, f"refs/heads/{a.branch}", cwd=clone, env=env)
        how = "git ls-remote"
    verified = rc == 0 and remote.split()[:1] == [head]
    url = f"https://github.com/{a.account}/{name}/tree/{a.branch}"
    L["fork"] = {"url": url, "push": push, "branch": a.branch, "base_tag": a.base_tag, "head": head,
                 "patches": [p.name for p in patches], "verified": verified, "verified_by": how, "at": now()}
    with (w.dir / "RECORD.md").open("a") as f:
        f.write(f"- {now()} fork {url}: {len(patches)} patch(es) on {a.base_tag}, head {head[:12]}, "
                f"pushed as {a.account}; head verified by {how}: {verified}\n")
    w.log(L, f"fork {url} head {head[:12]} verified={verified}")
    print(f"{url}\nhead {head}; verified by {how}: {verified}\ncite it in the draft, then `draft check` again")
    if not verified:
        w.save(L)
        print(f"WARNING: the pushed branch does not show head {head[:12]}: check by hand", file=sys.stderr)
        sys.exit(3)
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default="upstream")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("repo")
    p.add_argument("--channel", choices=["issue", "comment", "pr"], required=True)
    p.add_argument("--thread", type=int)
    p.add_argument("--on", choices=["issue", "pr"], default="issue", help="what --thread is")
    p.add_argument("--list", help="mailing-list address: plain text, the human sends it")
    p.add_argument("--user", required=True, help="the user's handle, @HANDLE")
    p = sub.add_parser("rules")
    p.add_argument("action", nargs="?", choices=["fetch", "classify"], default="fetch")
    p.add_argument("--local", help="a clone of the repo at its default branch, when gh api cannot read it")
    p.add_argument("--outcome", choices=OUTCOMES)
    p.add_argument("--form", help="the disclosure form the policy demands or forbids")
    p.add_argument("--note")
    p.add_argument("--security", action="store_true", help="a security problem: private channel")
    p.add_argument("--no-mentions", action="store_true", help="the project or thread asks for no @-mentions")
    p = sub.add_parser("search")
    p.add_argument("action", nargs="?", choices=["run", "mark"], default="run")
    p.add_argument("number", nargs="?")
    p.add_argument("mark", nargs="?", choices=MARKS)
    p.add_argument("why", nargs="?")
    p.add_argument("--keywords")
    p.add_argument("--archive-cmd", help="a command that searches the list archive for proposed patches")
    p = sub.add_parser("claim")
    p.add_argument("action", choices=["add", "list", "rm"])
    p.add_argument("n", nargs="?", type=int, help="for rm: the claim id")
    p.add_argument("--text")
    p.add_argument("--cmd")
    p.add_argument("--permalink")
    p.add_argument("--no-permalink")
    p.add_argument("--mask", action="append", help="regex masked before the re-run diff (timings)")
    p = sub.add_parser("env")
    p.add_argument("--text")
    p = sub.add_parser("draft")
    p.add_argument("action", choices=["check"])
    p.add_argument("file")
    p = sub.add_parser("judge")
    p.add_argument("action", nargs="?", choices=["run", "override"], default="run")
    p.add_argument("n", nargs="?", type=int)
    p.add_argument("--reason")
    p.add_argument("--judge-cmd", help="executable: JSON request on stdin, JSON verdict on stdout; `none` to skip")
    p.add_argument("--judge-model", choices=["auto", "jev", "von", "none"], default="auto")
    p.add_argument("--noul-threshold", type=float, default=0.9)
    p = sub.add_parser("approve")
    p.add_argument("--by", required=True)
    p.add_argument("--for", dest="for_file", help="the file whose text the user approved in chat")
    p.add_argument("--allow", action="append", help="a span `draft check` flagged as private, which the user's yes names; repeatable")
    p.add_argument("--why", help="with --allow: why the post needs it")
    p = sub.add_parser("post")
    p.add_argument("--account", required=True)
    p.add_argument("--head", help="for a PR: the branch, OWNER:BRANCH")
    p = sub.add_parser("fork", help="rung 3 of step 8: the patch series in a fork under the machine account")
    p.add_argument("--account", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--patches", required=True, help="a directory of git format-patch files, applied in name order")
    p.add_argument("--base-tag", required=True)
    p.add_argument("--author", required=True, help="\"Model (Harness) <machine-account email>\"")
    p.add_argument("--trailer", action="append", default=[], help="\"Assisted-By: slug\", Harness, Session-Id; repeatable")
    p.add_argument("--upstream-url", help="default https://github.com/OWNER/REPO.git")
    p.add_argument("--push-url", help="default https://github.com/ACCOUNT/REPO.git")
    sub.add_parser("status")
    p = sub.add_parser("graph")
    p.add_argument("--dot", action="store_true")
    p.add_argument("--mermaid", action="store_true")
    a = ap.parse_args(argv)
    w = Work(a.workdir)
    if a.command == "graph":
        print(dot() if a.dot else mermaid(), end="")
        return
    for flag, need_it in (("text", a.command == "claim" and getattr(a, "action", "") == "add"),
                          ("cmd", a.command == "claim" and getattr(a, "action", "") == "add"),
                          ("outcome", a.command == "rules" and a.action == "classify"),
                          ("form", a.command == "rules" and a.action == "classify"),
                          ("reason", a.command == "judge" and a.action == "override"),
                          ("why", a.command == "search" and a.action == "mark"),
                          ("why", a.command == "approve" and a.allow)):
        if need_it and not getattr(a, flag):
            ap.error(f"{a.command} {getattr(a, 'action', '')} needs --{flag}")
    run = {"init": cmd_init, "rules": cmd_rules, "search": cmd_search, "claim": cmd_claim, "env": cmd_env,
           "draft": cmd_draft, "judge": cmd_judge, "approve": cmd_approve, "post": cmd_post, "fork": cmd_fork}
    if a.command == "claim" and a.action == "rm" and a.n is None:
        ap.error("claim rm needs the claim id")
    # One command at a time per work dir: the lock is held from the read of
    # STATE.json through the command's own run (a claim's slow docker
    # command included) to the write, so parallel invocations queue instead
    # of overwriting each other's ledger entries.
    if a.command == "init":
        w.dir.mkdir(parents=True, exist_ok=True)
    lock = open(w.dir / ".lock", "w") if w.dir.is_dir() else None
    if lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        L = run[a.command](a, w) if a.command in run else w.load()
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(2)
    if L is not None:
        w.save(L)
        print(status_text(L, w))


if __name__ == "__main__":
    main()
