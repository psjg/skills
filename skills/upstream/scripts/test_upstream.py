#!/usr/bin/env python3
"""Tests for upstream.py, driven at the binary seam against the evals' fake gh.

    python3 -m unittest test_upstream.py        (from scripts/)

Each test builds a throwaway world with the evals' ``make_fixture.sh`` (a
tinyparse clone, the user's conventions, a fake ``gh`` that logs every call
with the account it ran as) and runs the script as a subprocess there. The
fake gh answers ``gh api`` with a 404, so the rules come from ``--local`` and
the render check is skipped, which is itself under test. The LLM judge is a
fake executable; the decision model runs only if an OpenRouter key is in the
environment. ``UPSTREAM_EVALS`` points at another evals directory; without
one the tests skip (installed copies of the skill carry no evals).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "upstream.py"
EVALS = Path(os.environ.get("UPSTREAM_EVALS", HERE.parent / "evals"))
REPO = "psjg/upstream-issue-eval-fixture"
CMD = """cd tinyparse && python3 -c 'from tinyparse import parse_line; print(parse_line("a,b,"))'"""
CLAIM = "`parse_line(\"a,b,\")` returns `['a', 'b']` on tinyparse 0.4.1: the `if cur:` guard drops the trailing empty field."
DISCLOSE = "*Written by an AI agent (Claude Opus 5.5, Claude Code, reasoning effort high) for {h}, who ran its commands and approved posting.*"
FOOTER = "*Prepared with the upstream skill (psjg/skills@abc1234), which lists the checks this report claims to pass.*"
FAKE_JUDGE = """#!{py}
# Blocks every draft line that mentions an import job: a planted claim no
# ledger entry supports.
import json, sys
req = json.load(sys.stdin)
bad = [l.strip() for l in req["draft"].splitlines() if "import job" in l]
print(json.dumps({{"findings": [{{"quote": q, "kind": "unsupported", "blocking": True, "why": "no ledger entry"}} for q in bad]}}))
"""


@unittest.skipUnless((EVALS / "make_fixture.sh").exists(), f"no evals at {EVALS}")
class Base(unittest.TestCase):
    """A fixture world per test, and the steps every flow shares."""
    policy = "allow"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upstream-test."))
        self.work = self.tmp / "work"
        subprocess.run(["sh", str(EVALS / "make_fixture.sh"), str(self.work), self.policy], check=True)
        # A PATH with the fake gh and python3 but no codex, so the default
        # judge is exercised as an installer without one would see it.
        pybin = self.tmp / "pybin"
        pybin.mkdir()
        (pybin / "python3").symlink_to(sys.executable)
        self.env = dict(os.environ, PATH=f"{self.work / 'bin'}:{pybin}:/usr/bin:/bin", GHSHIM_LOG=str(self.tmp / "gh.log"))
        for k in ("GH_TOKEN", "GITHUB_TOKEN"):
            self.env.pop(k, None)
        self.env |= {"GIT_AUTHOR_NAME": "PSJ", "GIT_AUTHOR_EMAIL": "psj@example.org",
                     "GIT_COMMITTER_NAME": "PSJ", "GIT_COMMITTER_EMAIL": "psj@example.org"}
        self.sha = subprocess.run(["git", "-C", str(self.work / "tinyparse"), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        self.link = f"https://github.com/{REPO}/blob/{self.sha}/tinyparse/__init__.py#L18-L19"
        self.judge = self.tmp / "fake-judge"
        self.judge.write_text(FAKE_JUDGE.format(py=sys.executable))
        self.judge.chmod(0o755)

    def up(self, *args, ok=True) -> str:
        p = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.work, env=self.env, capture_output=True, text=True)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode == 0, ok, f"upstream.py {' '.join(args)} -> exit {p.returncode}\n{out}")
        return out

    def state(self) -> dict:
        return json.loads((self.work / "upstream" / "STATE.json").read_text())

    def calls(self) -> list[dict]:
        log = self.tmp / "gh.log"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    def write_draft(self, extra: str = "", handle: str = "@psjg", footer: str = FOOTER, title: str = "# parse_line drops a trailing empty field") -> str:
        text = f"""{title}

{DISCLOSE.format(h=handle)}

{CLAIM}
The guard: {self.link}

## Version
tinyparse 0.4.1 at commit {self.sha}.

## Steps to reproduce
```
$ {CMD}
['a', 'b']
```

## Expected
`['a', 'b', '']`
{extra}
<details><summary>Environment</summary>

macOS 27.0 (26A428), Python 3.13.1
</details>

Related: #12 (closed) is about trailing whitespace, a different bug.

{footer}
"""
        (self.work / "draft.md").write_text(text)
        return text

    def walk_to_draft(self, channel=("--channel", "issue")):
        self.up("init", REPO, *channel, "--user", "@psjg")
        self.up("rules", "--local", "tinyparse")
        self.up("rules", "classify", "--outcome", "welcome", "--form", "a sentence saying an AI tool helped")
        self.up("search", "--keywords", "trailing empty field")
        self.up("search", "mark", "12", "related", "closed; trailing whitespace, a different bug")
        self.up("search", "mark", "7", "unrelated", "documentation")
        self.up("claim", "add", "--text", CLAIM, "--cmd", CMD, "--permalink", self.link)
        self.up("env", "--text", "MacBook Pro 14 (Mac15,3), M3, macOS 27.0 (26A428), Python 3.13.1")
        self.assertEqual(self.state()["state"], "draft")

    def walk_to_approval(self):
        self.walk_to_draft()
        self.write_draft()
        self.up("draft", "check", "draft.md")
        self.up("judge", "--judge-cmd", str(HERE / "judges" / "stub.sh"))
        self.assertEqual(self.state()["state"], "approval")


class Flow(Base):
    # --- the happy path ----------------------------------------------------

    def test_happy_path(self):
        self.walk_to_approval()
        self.up("approve", "--by", "@psjg")
        out = self.up("post", "--account", "psjg-claude")
        self.assertNotIn("ghp_fake", out)
        L = self.state()
        self.assertEqual(L["state"], "posted")
        self.assertTrue(L["post"]["verified"] and L["post"]["user_still_active"])
        creates = [c for c in self.calls() if c["argv"][:2] == ["issue", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["as"], "psjg-claude")
        self.assertTrue(creates[0]["body"].lstrip().startswith("*Written by an AI agent"))
        self.assertIn("parse_line drops a trailing empty field", creates[0]["argv"])
        self.assertFalse([c for c in self.calls() if c["argv"][:2] == ["auth", "switch"]])
        self.assertIn("issues/31", (self.work / "upstream" / "RECORD.md").read_text())
        self.assertEqual(L["draft"]["render"].get("details", 1), 1)  # the shim's crude render, when it has one
        self.assertIn("--state", [a for c in self.calls() if c["argv"][:2] == ["pr", "list"] for a in c["argv"]])
        self.up("post", "--account", "psjg-claude", ok=False)  # never twice

    def test_render_failure_is_a_note(self):
        self.walk_to_draft()
        self.write_draft()
        broken = self.tmp / "broken"
        broken.mkdir()
        (broken / "gh").write_text("#!/bin/sh\necho 'HTTP 404: Not Found' >&2\nexit 1\n")
        (broken / "gh").chmod(0o755)
        self.env["PATH"] = f"{broken}:{self.env['PATH']}"
        self.assertIn("render: skipped", self.up("draft", "check", "draft.md"))
        self.assertEqual(self.state()["state"], "judged")

    def test_gh_calls_carry_the_script_marker(self):
        self.up("init", REPO, "--channel", "issue", "--user", "@psjg")
        spy = self.tmp / "spy"
        spy.mkdir()
        (spy / "gh").write_text(f"#!/bin/sh\necho \"$GH_UPSTREAM_SCRIPT\" >> {self.tmp / 'marker'}\nexit 1\n")
        (spy / "gh").chmod(0o755)
        self.env["PATH"] = f"{spy}:{self.env['PATH']}"
        self.up("rules", "--local", "tinyparse")
        marks = (self.tmp / "marker").read_text().split()
        self.assertTrue(marks)
        self.assertEqual(set(marks), {"1"})

    def test_failing_or_unparseable_judge_blocks(self):
        self.walk_to_draft()
        self.write_draft()
        self.up("draft", "check", "draft.md")
        for cmd in ("false", "echo 'Looks fine to me!'", "echo '{\"findings\": \"none\"}'"):
            out = self.up("judge", "--judge-cmd", cmd)
            self.assertIn("BLOCKING other: \"judge output unparseable\"", out)
            self.assertEqual(self.state()["state"], "judged")
            self.up("approve", "--by", "@psjg", ok=False)

    def fake_bin(self, name: str, body: str):
        d = self.tmp / "fakes"
        d.mkdir(exist_ok=True)
        (d / name).write_text(f"#!/bin/sh\n{body}\n")
        (d / name).chmod(0o755)
        self.env["PATH"] = f"{d}:{self.env['PATH']}"

    def test_codex_adapter(self):
        # codex's last message (-o FILE) holds prose around the verdict, with
        # a brace inside a string: the first balanced object still parses.
        self.fake_bin("codex", 'while [ "$1" != -o ]; do shift; done; cat > /dev/null\n'
                      "printf 'Verdict below.\\n{\"findings\": [{\"quote\": \"The guard {x}\", \"kind\": \"inference\", "
                      "\"blocking\": true, \"why\": \"no command\"}]}\\nDone {.' > \"$2\"")
        self.walk_to_draft()
        self.write_draft()
        self.up("draft", "check", "draft.md")
        out = self.up("judge")  # codex on PATH: judges/codex.sh is the default
        self.assertIn("judges/codex.sh", out)
        self.assertIn('BLOCKING inference: "The guard {x}"', out)

    def test_cursor_adapter(self):
        self.fake_bin("cursor-agent", 'case "$*" in *"--model grok-4.7-medium --output-format text"*) ;; *) exit 9 ;; esac\n'
                      "echo 'I will not use tools. {not json} {\"findings\": []}'")
        self.walk_to_draft()
        self.write_draft()
        self.up("draft", "check", "draft.md")
        self.up("judge", "--judge-cmd", str(HERE / "judges" / "cursor.sh"))
        self.assertEqual(self.state()["state"], "approval")

    def test_default_judge_is_not_judged_without_tools(self):
        (self.work / "bin" / "codex").unlink(missing_ok=True)  # the fixture's stub judge
        self.walk_to_draft()
        self.write_draft()
        self.up("draft", "check", "draft.md")
        out = self.up("judge")
        L = self.state()
        self.assertEqual(L["judge"]["status"], "not judged", out)
        self.assertEqual(L["state"], "approval")
        self.assertIn("not judged", out)

    # --- refusals ----------------------------------------------------------

    def test_post_before_approval_refused(self):
        self.walk_to_approval()
        out = self.up("post", "--account", "psjg-claude", ok=False)
        self.assertIn("REFUSED", out)
        self.assertFalse([c for c in self.calls() if c["argv"][1:2] == ["create"]])

    def test_edit_after_approval_refused(self):
        self.walk_to_approval()
        self.up("approve", "--by", "@psjg")
        with (self.work / "draft.md").open("a") as f:
            f.write("One more line.\n")
        out = self.up("post", "--account", "psjg-claude", ok=False)
        self.assertIn("draft changed", out)
        self.assertFalse([c for c in self.calls() if c["argv"][1:2] == ["create"]])

    def test_approved_text_plus_mandatory_lines_posts(self):
        """Eval 2: the user approved issue-draft.md as it was; the machine's
        checks then require the disclosure, Related and footer lines. Adding
        exactly those keeps the approval; any other change voids it."""
        self.walk_to_draft()
        full = self.write_draft()
        mandatory = (DISCLOSE.format(h="@psjg"), "Related: #12 (closed) is about trailing whitespace, a different bug.", FOOTER)
        bare = "\n".join(l for l in full.splitlines() if l not in mandatory) + "\n"
        (self.work / "issue-draft.md").write_text(bare)
        self.up("approve", "--by", "@psjg", "--for", "issue-draft.md")  # before any check: the user's yes in chat
        (self.work / "issue-draft.md").write_text(full)
        self.up("draft", "check", "issue-draft.md")
        self.up("judge", "--judge-cmd", str(HERE / "judges" / "stub.sh"))
        self.assertEqual(self.state()["state"], "approved")
        # Any other change: back to approval, with the diff, and no post.
        (self.work / "issue-draft.md").write_text(full.replace("## Expected", "## Expected result"))
        self.up("draft", "check", "issue-draft.md")
        self.up("judge", "--judge-cmd", str(HERE / "judges" / "stub.sh"))
        out = self.up("post", "--account", "psjg-claude", ok=False)
        self.assertIn("+ ## Expected result", out)
        self.assertIn("- ## Expected", out)
        self.assertEqual(self.state()["state"], "approval")
        self.assertFalse([c for c in self.calls() if c["argv"][1:2] == ["create"]])
        # Restored: the approval holds again and the post goes out.
        (self.work / "issue-draft.md").write_text(full)
        self.up("draft", "check", "issue-draft.md")
        self.up("judge", "--judge-cmd", str(HERE / "judges" / "stub.sh"))
        self.up("post", "--account", "psjg-claude")
        creates = [c for c in self.calls() if c["argv"][:2] == ["issue", "create"]]
        self.assertEqual([c["as"] for c in creates], ["psjg-claude"])

    def test_approve_by_someone_else_refused(self):
        self.walk_to_approval()
        self.up("approve", "--by", "@maintainer", ok=False)

    def test_draft_without_footer_fails(self):
        self.walk_to_draft()
        self.write_draft(footer="Thanks!")
        out = self.up("draft", "check", "draft.md")
        self.assertIn("FAIL  footer last", out)
        self.assertEqual(self.state()["state"], "draft")
        self.up("judge", "--judge-cmd", "none", ok=False)

    def test_claim_output_changed_fails_with_diff(self):
        self.walk_to_draft()
        (self.work / "numbers.txt").write_text("14 passed\n")
        self.up("claim", "add", "--text", "The suite reports 14 passed.", "--cmd", "cat numbers.txt", "--no-permalink", "a local run")
        (self.work / "numbers.txt").write_text("13 passed, 1 failed\n")
        self.write_draft(extra="\nThe suite reports 14 passed.\n")
        out = self.up("draft", "check", "draft.md")
        self.assertIn("FAIL  claim 2 re-run", out)
        self.assertIn("+13 passed, 1 failed", out)
        self.assertEqual(self.state()["state"], "draft")

    def test_third_party_mention_fails(self):
        self.walk_to_draft()
        self.write_draft(extra="\ncc @maintainer\n")
        out = self.up("draft", "check", "draft.md")
        self.assertIn("FAIL  no other mentions: @maintainer", out)

    def test_mention_in_code_is_not_a_mention(self):
        self.walk_to_draft()
        self.write_draft(extra="\nA `@decorator` in a code span is code.\n")
        self.assertIn("PASS  no other mentions", self.up("draft", "check", "draft.md"))

    def test_hedging_word_in_prose_fails(self):
        self.walk_to_draft()
        self.write_draft(extra="\nThis probably affects 0.4.0 too.\n")
        out = self.up("draft", "check", "draft.md")
        self.assertIn("FAIL  no hedging words: 'probably'", out)

    def test_hedging_word_in_code_passes(self):
        self.walk_to_draft()
        self.write_draft(extra="\n```\nassert parse_line('a,') == ['a', ''], 'should keep the field'\n```\n")
        self.assertIn("PASS  no hedging words", self.up("draft", "check", "draft.md"))

    def test_judge_request_marks_contract_lines(self):
        """A draft whose every sentence is ledger-backed, plus the contract
        lines: the judge (a capturing fake, no model) receives them as
        contract_lines, and the adapters' prompt tells it not to judge them."""
        self.walk_to_draft()
        env = "**Environment.** MacBook Pro 14 (Mac15,3), M3, macOS 27.0 (26A428), Python 3.13.1"
        related = "Related: #12 (closed) is about trailing whitespace, a different bug."
        disclosure = DISCLOSE.format(h="@psjg")
        (self.work / "draft.md").write_text(f"# parse_line drops a trailing empty field\n\n{disclosure}\n\n{CLAIM}\n"
                                            f"The guard: {self.link}\n\n{env}\n\n{related}\n\n{FOOTER}\n")
        self.up("draft", "check", "draft.md")
        capture = self.tmp / "capture-judge"
        capture.write_text(f"#!/bin/sh\ncat > {self.tmp / 'request.json'}\necho '{{\"findings\": []}}'\n")
        capture.chmod(0o755)
        self.up("judge", "--judge-cmd", str(capture))
        self.assertEqual(self.state()["state"], "approval")
        req = json.loads((self.tmp / "request.json").read_text())
        self.assertEqual(req["contract_lines"], [disclosure, related, env, FOOTER])
        self.assertIn("never judge them", req["instructions"])
        prompt = subprocess.run([sys.executable, str(HERE / "judges" / "verdict.py"), "prompt"], input=json.dumps(req),
                                capture_output=True, text=True, check=True).stdout
        self.assertIn("CONTRACT LINES (required boilerplate: never judge or quote these):\n" + json.dumps(req["contract_lines"], indent=1), prompt)
        sys.path.insert(0, str(HERE))
        import upstream
        body = (self.work / "upstream" / "body.md").read_text()
        self.assertEqual(upstream.sentences(body, {}), [CLAIM, f"The guard: {self.link}"])

    def test_judge_blocks_planted_sentence(self):
        self.walk_to_draft()
        self.write_draft(extra="\nThe same bug crashed the user's import job twice last week.\n")
        self.up("draft", "check", "draft.md")
        out = self.up("judge", "--judge-cmd", str(self.judge))
        self.assertIn("BLOCKING unsupported", out)
        self.assertEqual(self.state()["state"], "judged")
        self.up("approve", "--by", "@psjg", ok=False)
        log = [json.loads(l) for l in (self.work / "upstream" / "judge.log").read_text().splitlines()]
        self.assertTrue(any("import job" in e.get("quote", "") for e in log))
        # The human may override, and that is logged too.
        self.up("judge", "override", "1", "--reason", "the user saw both crashes and wants it in")
        self.assertEqual(self.state()["state"], "approval")
        # Or the agent removes the sentence, and the judge passes.
        self.write_draft()
        self.up("draft", "check", "draft.md")
        self.up("judge", "--judge-cmd", str(self.judge))
        self.assertEqual(self.state()["state"], "approval")

    def test_search_before_rules_refused(self):
        self.up("init", REPO, "--channel", "issue", "--user", "@psjg")
        self.up("search", "--keywords", "x", ok=False)
        self.up("rules", "classify", "--outcome", "welcome", "--form", "x", ok=False)  # nothing fetched yet

    def test_parallel_claims_both_land_and_rm(self):
        self.walk_to_draft()
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "claim", "add", "--text", f"t{k}", "--cmd", f"sleep 1; echo {k}",
                                   "--no-permalink", "x"], cwd=self.work, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                 for k in (2, 3)]
        for p in procs:
            self.assertEqual(p.wait(), 0, p.stderr.read())
        claims = self.state()["claims"]
        self.assertEqual(sorted(c["text"] for c in claims), [CLAIM, "t2", "t3"])
        self.assertEqual(len({c["id"] for c in claims}), 3)
        self.up("claim", "rm", "2")
        self.up("claim", "rm", "2", ok=False)
        self.up("claim", "add", "--text", "t4", "--cmd", "echo 4", "--no-permalink", "x")
        self.assertEqual([c["id"] for c in self.state()["claims"]], [1, 3, 4])

    def test_unmarked_hit_blocks_evidence(self):
        self.up("init", REPO, "--channel", "issue", "--user", "@psjg")
        self.up("rules", "--local", "tinyparse")
        self.up("rules", "classify", "--outcome", "welcome", "--form", "x")
        self.up("search", "--keywords", "trailing")
        self.up("search", "mark", "12", "unrelated", "whitespace")
        self.assertIn("hit #7 is not marked", self.up("status"))
        self.up("claim", "add", "--text", "t", "--cmd", "true", "--no-permalink", "none", ok=False)

    def test_unpinned_permalink_refused(self):
        self.walk_to_draft()
        self.up("claim", "add", "--text", "t", "--cmd", "true", "--permalink",
                f"https://github.com/{REPO}/blob/main/tinyparse/__init__.py#L18", ok=False)

    def test_refuse_outcome_gathers_facts_then_stops(self):
        """A channel that refuses AI content still gets the facts (search,
        claims with permalinks) for the user to write from; only the draft
        and everything after it are off."""
        self.up("init", REPO, "--channel", "issue", "--user", "@psjg")
        self.up("rules", "--local", "tinyparse")
        self.up("rules", "classify", "--outcome", "refuse", "--form", "a human writes the description")
        self.assertEqual(self.state()["state"], "search")
        self.up("search", "--keywords", "trailing empty field")
        self.up("search", "mark", "12", "unrelated", "whitespace")
        self.up("search", "mark", "7", "unrelated", "docs")
        self.up("claim", "add", "--text", CLAIM, "--cmd", CMD, "--permalink", self.link)
        self.up("env", "--text", "macOS 27.0")
        self.assertEqual(self.state()["state"], "refused")
        facts = self.up("claim", "list")
        self.assertIn(self.link, facts)
        self.assertIn("> ['a', 'b']", facts)
        self.write_draft()
        self.up("draft", "check", "draft.md", ok=False)

    def test_issue_only_stops_a_pr(self):
        self.up("init", REPO, "--channel", "pr", "--user", "@psjg")
        self.up("rules", "--local", "tinyparse")
        self.up("rules", "classify", "--outcome", "issue-only", "--form", "vouch first")
        self.assertEqual(self.state()["state"], "pr-refused")

    def test_mailing_list_forbids_html(self):
        self.up("init", REPO, "--channel", "issue", "--list", "~psjg/tinyparse-devel@lists.sr.ht", "--user", "@psjg")
        self.up("rules", "--local", "tinyparse")
        self.up("rules", "classify", "--outcome", "welcome", "--form", "say so in the report")
        self.up("search", "--keywords", "trailing")
        self.assertIn("list archive", self.up("status"))
        self.up("search", "--archive-cmd", "echo no patches found")
        self.up("search", "mark", "12", "unrelated", "whitespace")
        self.up("search", "mark", "7", "unrelated", "docs")
        self.up("claim", "add", "--text", CLAIM, "--cmd", CMD, "--permalink", self.link)
        self.up("env", "--text", "macOS 27.0")
        self.write_draft()
        self.assertIn("FAIL  plain text: </details>", self.up("draft", "check", "draft.md"))


class Fork(Base):
    """Rung 3: a patch series on the upstream tag, in the machine account's fork."""

    def setUp(self):
        super().setUp()
        up = self.work / "tinyparse"
        subprocess.run(["git", "-C", str(up), "tag", "v0.4.1"], check=True)
        scratch = self.tmp / "scratch"
        subprocess.run(["git", "clone", "-q", str(up), str(scratch)], check=True)
        src = scratch / "tinyparse" / "__init__.py"
        src.write_text(src.read_text().replace("    if cur:\n        fields.append(cur)", "    if cur or line.rstrip('\\n').endswith(sep):\n        fields.append(cur)"))
        subprocess.run(["git", "-C", str(scratch), "commit", "-qam", "fix(parse): keep a trailing empty field"], check=True, env=self.env)
        self.patches = self.tmp / "patches"
        subprocess.run(["git", "-C", str(scratch), "format-patch", "-q", "v0.4.1..HEAD", "-o", str(self.patches)], check=True)
        self.bare = self.tmp / "fork.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        self.args = ["fork", "--account", "psjg-claude", "--branch", "fix-trailing-field", "--patches", str(self.patches),
                     "--base-tag", "v0.4.1", "--author", "Claude Opus 5.5 (Claude Code) <1+psjg-claude@users.noreply.github.com>",
                     "--trailer", "Assisted-By: claude-opus-5-5", "--upstream-url", str(up), "--push-url", str(self.bare)]

    def test_fork_after_approval(self):
        self.walk_to_approval()
        self.up(*self.args, ok=False)  # not before the user's go
        self.up("approve", "--by", "@psjg")
        out = self.up(*self.args)
        self.assertNotIn("ghp_fake", out)
        forks = [c for c in self.calls() if c["argv"][:2] == ["repo", "fork"]]
        self.assertEqual([c["as"] for c in forks], ["psjg-claude"])
        log = subprocess.run(["git", "-C", str(self.bare), "log", "--format=%an <%ae>%n%B", "fix-trailing-field", "-1"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("Claude Opus 5.5 (Claude Code)", log)
        self.assertIn("Assisted-By: claude-opus-5-5", log)
        self.assertIn("fix(parse): keep a trailing empty field", log)
        self.assertNotIn("Signed-off-by", log)
        L = self.state()
        self.assertTrue(L["fork"]["verified"])
        self.assertEqual(L["state"], "approved")  # a fork does not post the issue
        self.assertIn("tree/fix-trailing-field", (self.work / "upstream" / "RECORD.md").read_text())

    def test_fork_refuses_signed_off_and_broken_patches(self):
        self.walk_to_approval()
        self.up("approve", "--by", "@psjg")
        patch = next(self.patches.glob("*.patch"))
        text = patch.read_text()
        patch.write_text(text.replace("---\n", "\nSigned-off-by: Agent <a@example.org>\n---\n", 1))
        self.assertIn("DCO", self.up(*self.args, ok=False))
        patch.write_text(text.replace("if cur:", "if curr:"))  # context that is not upstream
        self.assertIn("does not apply", self.up(*self.args, ok=False))
        self.assertFalse([c for c in self.calls() if c["argv"][:2] == ["repo", "fork"]])


class Dogfood(Base):
    """The shape of the first real use (Mozart 1.4): an issue with a diff
    inline, a fork carrying a series of plain -p1 patches with prose headers
    on a release tag, then a comment on an existing PR thread citing both.
    Claims are a docker run and a grep on the checked-out tag."""

    def setUp(self):
        super().setUp()
        up = self.work / "tinyparse"
        subprocess.run(["git", "-C", str(up), "tag", "v0.4.1"], check=True)
        self.patches = self.tmp / "patches"
        self.patches.mkdir()
        scratch = self.tmp / "scratch"
        subprocess.run(["git", "clone", "-q", str(up), str(scratch)], check=True)
        edits = [("tinyparse/__init__.py", "    if cur:\n", "    if cur or line.rstrip('\\n').endswith(sep):\n",
                  "Patch 01: keep a trailing empty field\n\nWhy: parse_line('a,b,') drops the last field.\n"),
                 ("README.md", "Split CSV-like lines.", "Split CSV-like lines, keeping empty fields.",
                  "Patch 02: say so in the README\n")]
        for k, (path, old, new, header) in enumerate(edits, 1):
            f = scratch / path
            f.write_text(f.read_text().replace(old, new))
            diff = subprocess.run(["git", "-C", str(scratch), "diff"], capture_output=True, text=True, check=True).stdout
            (self.patches / f"{k:02d}-{Path(path).stem}.patch").write_text(header + "\n" + diff)
            subprocess.run(["git", "-C", str(scratch), "commit", "-qam", f"p{k}"], check=True, env=self.env)
        # docker as the claims use it: a deterministic test-suite tally.
        d = self.tmp / "fakes"
        d.mkdir()
        (d / "docker").write_text("#!/bin/sh\necho 'oztest: 621/621 passed'\n")
        (d / "docker").chmod(0o755)
        # The evals' shim can not answer the comments API; this wrapper does,
        # from its own log, and hands everything else to the shim.
        (d / "gh").write_text(f"""#!/bin/sh
case "$*" in "api repos/"*"/issues/comments/"*)
  python3 -c 'import json,sys; c=[json.loads(l) for l in open(sys.argv[1])]
print(json.dumps({{"user": {{"login": [x for x in c if x["argv"][1:2] == ["comment"]][-1]["as"]}}}}))' "$GHSHIM_LOG"; exit 0 ;;
esac
exec {self.work / 'bin' / 'gh'} "$@"
""")
        (d / "gh").chmod(0o755)
        self.env["PATH"] = f"{d}:{self.env['PATH']}"
        self.bare = self.tmp / "fork.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        self.fork_url = f"https://github.com/psjg-claude/upstream-issue-eval-fixture/tree/fix-trailing-field"

    def evidence(self, wd: str):
        self.up("--workdir", wd, "rules", "--local", "tinyparse")
        self.up("--workdir", wd, "rules", "classify", "--outcome", "welcome", "--form", "a sentence saying an AI tool helped")
        self.up("--workdir", wd, "search", "--keywords", "trailing empty field")
        self.up("--workdir", wd, "search", "mark", "12", "unrelated", "whitespace")
        self.up("--workdir", wd, "search", "mark", "7", "unrelated", "docs")
        self.up("--workdir", wd, "claim", "add", "--text", "The patched build passes 621/621.",
                "--cmd", "docker run --rm tinyparse-native oztest --ignores=dp", "--no-permalink", "a local docker build")
        self.up("--workdir", wd, "claim", "add", "--text", "At the tag the guard reads `if cur:`.",
                "--cmd", "git -C tinyparse grep -n 'if cur' v0.4.1 -- tinyparse/__init__.py", "--permalink", self.link)
        self.up("--workdir", wd, "env", "--text", "native aarch64, debian:bookworm, gcc 12.2.0")

    def body(self, cites: str) -> str:
        return (f"{DISCLOSE.format(h='@psjg')}\n\nAt the tag the guard reads `if cur:`.\n{self.link}\n\n"
                f"The patched build passes 621/621.\n\n{cites}\n\nRelated: none found by `gh issue list --state all`.\n\n{FOOTER}\n")

    def test_issue_fork_then_comment(self):
        self.up("--workdir", "issue", "init", REPO, "--channel", "issue", "--user", "@psjg")
        self.evidence("issue")
        (self.work / "ISSUE.md").write_text("# parse_line drops a trailing empty field\n\n" + self.body(f"The series is at {self.fork_url}."))
        self.up("--workdir", "issue", "draft", "check", "ISSUE.md")
        self.up("--workdir", "issue", "judge", "--judge-cmd", str(HERE / "judges" / "stub.sh"))
        self.up("--workdir", "issue", "approve", "--by", "@psjg")
        self.up("--workdir", "issue", "fork", "--account", "psjg-claude", "--branch", "fix-trailing-field",
                "--patches", str(self.patches), "--base-tag", "v0.4.1",
                "--author", "Claude Opus 5.5 (Claude Code) <1+psjg-claude@users.noreply.github.com>",
                "--trailer", "Assisted-By: claude-opus-5-5", "--upstream-url", str(self.work / "tinyparse"), "--push-url", str(self.bare))
        log = subprocess.run(["git", "-C", str(self.bare), "log", "--format=%s|%an|%(trailers:key=Assisted-By,valueonly)", "fix-trailing-field"],
                             capture_output=True, text=True, check=True).stdout.split("\n")
        log = [l for l in log if l]
        self.assertTrue(log[0].startswith("Patch 02: say so in the README|Claude Opus 5.5 (Claude Code)|claude-opus-5-5"), log)
        self.assertTrue(log[1].startswith("Patch 01: keep a trailing empty field|"), log)
        issue_url = self.up("--workdir", "issue", "post", "--account", "psjg-claude").splitlines()[0]
        self.assertIn("/issues/31", issue_url)
        self.assertIn(self.fork_url, (self.work / "issue" / "RECORD.md").read_text())

        self.up("--workdir", "comment", "init", REPO, "--channel", "comment", "--thread", "201", "--on", "pr", "--user", "@psjg")
        self.evidence("comment")
        (self.work / "COMMENT.md").write_text(self.body(f"The fork: {self.fork_url}; the issue: {issue_url}."))
        self.up("--workdir", "comment", "draft", "check", "COMMENT.md")
        self.up("--workdir", "comment", "judge", "--judge-cmd", str(HERE / "judges" / "stub.sh"))
        self.up("--workdir", "comment", "approve", "--by", "@psjg")
        self.up("--workdir", "comment", "post", "--account", "psjg-claude")
        L = json.loads((self.work / "comment" / "STATE.json").read_text())
        self.assertTrue(L["post"]["verified"], L["post"])
        posts = [(c["as"], c["argv"][:3]) for c in self.calls() if c["argv"][1:2] in (["create"], ["comment"]) or c["argv"][:2] == ["repo", "fork"]]
        self.assertEqual(posts, [("psjg-claude", ["repo", "fork", REPO]), ("psjg-claude", ["issue", "create", "-R"]),
                                 ("psjg-claude", ["pr", "comment", "201"])])


class WontFix(Base):
    policy = "wontfix"

    def test_duplicate_stops(self):
        self.up("init", REPO, "--channel", "issue", "--user", "@psjg")
        self.up("rules", "--local", "tinyparse")
        self.up("rules", "classify", "--outcome", "welcome", "--form", "x")
        out = self.up("search", "--keywords", "trailing empty field")
        self.assertIn("#12 [CLOSED/NOT_PLANNED]", out)
        self.up("search", "mark", "12", "duplicate", "closed as not planned: by design since 0.3")
        self.assertEqual(self.state()["state"], "duplicate")
        self.up("claim", "add", "--text", "t", "--cmd", "true", "--no-permalink", "x", ok=False)


class NoMentions(Base):
    policy = "no-mentions"

    def test_handle_without_at(self):
        self.up("init", REPO, "--channel", "comment", "--thread", "7", "--on", "pr", "--user", "@psjg")
        out = self.up("rules", "--local", "tinyparse")
        self.assertIn("thread:", out)  # the maintainer's no-mentions request is among the hits
        self.up("rules", "classify", "--outcome", "welcome", "--form", "x", "--no-mentions")
        self.up("search", "--keywords", "sep")
        self.up("search", "mark", "12", "unrelated", "whitespace")
        self.up("search", "mark", "7", "related", "this thread")
        self.up("claim", "add", "--text", CLAIM, "--cmd", CMD, "--permalink", self.link)
        self.up("env", "--text", "macOS 27.0")
        self.write_draft(title="", extra="\nSee #7.\n")
        self.assertIn("FAIL  user handle", self.up("draft", "check", "draft.md"))
        self.write_draft(title="", handle="psjg", extra="\nSee #7.\n")
        out = self.up("draft", "check", "draft.md")
        self.assertNotIn("FAIL", out)
        self.assertIn("deviation", out)


class Pure(unittest.TestCase):
    """The two contracts a subprocess can not observe."""

    def setUp(self):
        sys.path.insert(0, str(HERE))
        import upstream
        self.u = upstream

    def test_mermaid_in_skill_is_generated(self):
        skill = (HERE.parent / "SKILL.md").read_text()
        block = re.search(r"<!-- generated by scripts/upstream.py graph --mermaid.*?-->\n```mermaid\n(.*?)```", skill, re.S)
        self.assertIsNotNone(block, "SKILL.md has no generated mermaid block")
        self.assertEqual(block.group(1), self.u.mermaid())

    def test_decision_request_carries_only_the_ledger(self):
        claim = {"id": 1, "text": "It returns ['a', 'b'].", "cmd": "python3 -c ...", "cwd": "/Users/secret/work",
                 "output": "['a', 'b']\n", "permalink": "https://example.org/x", "exit": 0, "masks": [], "at": "t"}
        choice, noul = self.u.decision_requests("It returns ['a', 'b'].", [claim])
        self.assertEqual(set(choice), {"state", "question"})
        self.assertEqual(choice["state"], "It returns ['a', 'b'].")
        n = noul("c1")
        self.assertEqual(set(json.loads(n["state"])), {"sentence", "command", "output", "permalink"})
        self.assertNotIn("/Users/secret", json.dumps([choice, n]))


if __name__ == "__main__":
    unittest.main()
