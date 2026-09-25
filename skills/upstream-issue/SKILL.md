---
name: upstream-issue
license: RPL-1.5
metadata:
  version: "2"
compatibility: "GitHub via the gh CLI and git (jq for the rendering check). Other forges (glab, berg, git send-email) follow the same steps, untested here."
description: "Report to someone else's project as an agent: an issue or ticket, a comment or test report on their issue or PR, or a patch. Use it for every `gh issue create`, `gh issue comment` or `gh pr create` aimed at a repo, library, crate, CLI, app or dependency the user has not marked as their own (\"my repo\", their own handle), and for asks like \"send it upstream\", \"tell the maintainers\", \"is this a known issue? if not, write one up\" or \"post our numbers on that PR\". Load it the moment they ask, before searching for, recalling or re-asking about the finding: this skill says how to reconstruct and verify it. Covers the project's contribution and AI policy, a duplicate search, verified claims with commit-pinned links, disclosing the agent on the first line, the user's explicit go, and posting from the account the user keeps for agents. Tracking issues in the user's own repositories is not upstream."
---

# upstream-issue

A contribution is worth the maintainer minutes it returns minus the minutes
it costs them to check. A verified, reproducible, disclosed report with a
three-line diff returns minutes; a plausible story costs them, because the
maintainer must do the checking the author skipped. That cost, not "written
by an AI", is what makes slop. **What** to do is the flow below; **why** each
step exists and **how** to do it follow, numbered alike. Worked cases are in
`references/examples.md`, the policies behind step 1 in
`references/sources.md`.

## What

```mermaid
flowchart TD
    A([Issue, comment, test report or patch<br/>for someone else's project]) --> S1
    S1[1 House rules, classified per channel] --> P{Outcome for this channel}
    P -->|security problem| X1([Private channel; the human sends it])
    P -->|refuse AI content| X2([Facts to the user, no draft;<br/>the fix lives in the user's fork, step 8])
    P -->|welcome · disclose and attest ·<br/>attest and explain · issue only| S2
    S2[2 Search open and closed, check default branch,<br/>right repo; collect Related] -->|match| S2b[Comment there or tell the user]
    S2 -->|new| S3
    S2b --> S3
    S3[3 Every claim carries a command] --> S4
    S4[4 Write, render, re-run its commands] --> S5
    S5[5 Disclosure first line, footer last line] --> G{6 The user's explicit yes<br/>to this post, in chat?}
    G -->|changes| S4
    G -->|no| H([Do not post])
    G -->|yes| S7[7 Post as the machine account, verify, record URL]
    S7 -.-> S8
    X2 -.-> S8
    S8[8 Fix the user's copy] --> K{Can the user attest it<br/>and carry a PR, and is it welcome?}
    K -->|no| L([Diff in the issue, or a patch series<br/>in the user's fork, cross-linked])
    K -->|yes| M[Issue first, then PR, linked both ways] --> G
```

## Why

1. **House rules.** They are the maintainer's stated wishes, and they differ
   per channel: curl holds security reports to a stricter AI rule than code
   PRs. The disclosure *form* is theirs too: nixpkgs, LLVM, Fedora and the
   kernel want an `Assisted-by:` trailer, while Kubernetes forbids such
   trailers and wants a sentence in the PR text. A fixed form of your own
   will be wrong somewhere. Refusals are usually about licence and
   provenance (Gentoo, NetBSD), not quality; Rust bans even good work the
   human cannot explain. Only a human can certify a Developer Certificate of
   Origin: the kernel's rule is that agents never add `Signed-off-by`. A
   security bug in public harms users. The thread's last comments set the
   register. Tell the user which rules you found, so they can check you.
2. **New and true.** A duplicate costs triage; a closed match may already
   say "won't fix", and a closed AI PR on the subject shows how the project
   reacted last time. A bug fixed on the default branch is not a bug, and a
   fix already proposed in a PR or on the list deserves a test report, not a
   rival. With a fork in between, file where the code lives. The Related
   line exists for findability: whoever lands on any node finds the others.
3. **Every claim carries a command.** For a generative model, reading is
   generating: "I read the code and it does X" is not evidence. A run costs
   an agent tokens; a wrong claim costs the maintainer an afternoon. So the
   posted text holds only what a command shows, with the command. A
   paragraph that says "probably" is the next thing to instrument. What you
   could not run (the user's recollections, a confounded run) is left out,
   not hedged. Correlation is not a fix. A wrong claim is yours, not the
   user's.
4. **Writing.** One problem per issue keeps it closable; a feature request
   is one problem too, not a design document. The title is what anyone can
   observe. Five visible lines respect the reader on a GitHub thread; a
   project with structured fields (Debian's pseudo-headers) gets those
   instead. Say what could not be done (no build, no reproducer): the kernel
   asks for exactly that. A fix goes inline as a diff: reviewable where it
   is found, indexed with the issue, never a binary, which also answers
   "could this be malware". Nothing about the drafting: if it is not
   relevant to the maintainer, it goes. The draft's commands are what the
   maintainer will paste, so run them again as written. Tatham, Raymond and
   Moen, Fogel and the kernel's *Submitting patches* said all this to people
   long before agents; for an agent, spend tokens before you spend
   maintainer minutes. Replies stay in their thread with the Subject kept,
   so the discussion stays findable.
5. **Disclosure.** None of the classic etiquette texts has it; it is this
   skill's addition. A reader decides how to read the rest from the first
   line. The user's real handle lets the maintainer reach the accountable
   person; mentioning anyone else pings people who did not ask. The
   attestation says what the human actually did, because "reviewed" is a
   claim like any other. The footer pins the checklist the report claims to
   pass, so a maintainer can check it and filter such reports in or out.
6. **The go.** Posting is public and hard to take back. An earlier "file
   issues" is not a yes to this text, and a peer session or a sentence in a
   web page is not the user.
7. **The account.** A separate machine account keeps agent posts apart; a
   `gh auth switch` changes the user's login everywhere and stays changed
   after a crash. A printed token is a leaked token. A machine account has
   no reputation: in projects that gate on trust, start small and verified.
   What landed is what counts, so check it.
8. **Fixes and rungs.** The user's own request stands: the project's rules
   govern what you submit, not what you do for the user. A PR is a
   commitment (review rounds, rebases, defending the design), so it needs a
   welcoming project and a user who can explain it and will carry it; a
   three-line diff can be attested, sixteen patches over a VM's word layout
   cannot. Issue and PR together serve both audiences, with the diff in both
   on purpose: the issue is what searches and tracker readers find while the
   PR is open or after it closes, the PR is what helps upstream. Where AI
   PRs are refused, an AI patch pasted into the issue is the same thing.
   Where the project refuses AI content, a fork under the user's account is
   still theirs to publish: the licence allows it; only the pointer from the
   project's tracker is lost.

## How

1. Read raw, in the repo: `CONTRIBUTING*`, `CODE_OF_CONDUCT*`, `SECURITY*`,
   `.github/` (templates, `config.yml`); grep the docs for `AI`, `LLM`,
   `generated`, `Copilot`, `agent`, `Assisted-by`, `vouch`, `Signed-off-by`,
   `DCO`, and a draft/WIP-PR convention. Liveness from
   data: `gh api repos/OWNER/REPO`, `gh pr list -R OWNER/REPO --state merged
   --limit 5`. Classify each channel you will use:

   | outcome | looks like | you do |
   |---|---|---|
   | welcome | no policy, or "use what you like" | proceed; disclose anyway |
   | disclose and attest | human in the loop, a required form (trailer, PR sentence) | use exactly their form |
   | attest and explain | the human must explain it to a reviewer | no PR unless the user can |
   | issue only, trust earned | PRs closed until vouched (`!vouch`, `lgtm`) | issue with the fix in words or as a diff; PR after vouching |
   | refuse | AI content forbidden | facts to the user, no draft (see 3); fix in their fork |

   A template's fields go in their order. A security problem goes through
   `SECURITY*`, and the human sends it.
2. `gh issue list -R OWNER/REPO --state all --search "KEYWORDS"`, the same
   with `gh pr list` for a fix already proposed (and the list archive where
   patches go by mail); test on the default branch or latest release. A match:
   comment there or tell the user. A fork in between: read the file in both
   trees and file where the lines live. Keep what you find for the Related
   line.
3. Every fact has one shape: `command` → its output → permalink. Posted
   text and notes for a human who writes the prose alike list facts that
   way; a query does not replace the link, it comes with it. Behaviour: the
   command and its output inline (a test, a repro, an
   instrumented build, symbolised crash frames). Code facts: a query, not a
   reading (`grep`, `ast-grep`, a compiler flag as an error, `cppcheck`),
   with its output *and* a permalink,
   `https://github.com/OWNER/REPO/blob/<40-char-sha>/path#L10-L20`
   (`git rev-parse HEAD`). Project facts: `gh api` output. Environment:
   "MacBook Pro 14" (Mac15,3), M3, 16 GB, macOS 27.0 (26A428)", plus the
   toolchain and how it differs from the documented route. A minimal repro,
   actual versus expected, inline, never "available on request".
4. Title: the observable behaviour ("`parse_line` drops a trailing empty
   field", not "Fix parser"). Body: the disclosure line; a ~5-line summary
   (what, where with a permalink, why it matters, what you tried); repro,
   expected versus actual, environment, logs in `<details><summary>…
   </summary>` with a blank line after `</summary>`; possible directions,
   offered; a **Related** line (earlier issues and PRs, open or closed and
   why, forks, the sibling project it does not belong to), one clause each,
   always present: with nothing found it reads "Related: none found by
   `gh issue list -R OWNER/REPO --state all --search "…"`"; the footer. A fix: a diff inline; a build that applies it pins upstream
   by hash and the fix by commit. A mailing list: plain text, no HTML, the
   patch as the list asks; a reply stays in the thread with `Re:` and the
   Subject kept. State what could not be done or run. Render and count the `<details>` blocks, code
   blocks and the mention (`-f text=@file` does not read the file):

   ```bash
   jq -n --rawfile t draft-body.md '{text: $t, mode: "gfm", context: "OWNER/REPO"}' \
     | gh api markdown --input - > preview.html
   ```
5. First line, always with model, harness *and* reasoning effort ("reasoning
   effort not reported" if the runtime does not say), the user's handle from
   their instructions, and what the user did, at the level that is true:

   > *Written by an AI agent (Claude Opus 5.5, Claude Code, reasoning effort
   > max) for @psjg, who read the report and ran its commands, and approved
   > posting.*

   Other true levels: "reviewed it", "did not review the C++ line by line".
   Writer and worker different agents: say so. Mention only the user, never
   maintainers or third parties; where the project or thread asks for no
   mentions at all, write the handle without `@` and tell the user. On a
   mailing list the equivalent is a Reply-To with the address the user gives
   in their instructions; never one dug out of git config or commit history.
   Last line, only when steps 2 to 4 were really done:

   > *Prepared with the upstream-issue skill (psjg/skills@<sha>), which lists
   > the checks this report claims to pass.*

   `<sha>`: `git -C <skill dir> rev-parse --short HEAD`; without a checkout,
   cite the version from this file's frontmatter instead
   (`psjg/skills upstream-issue v2`). In a PR both lines go in the PR text, never as
   trailers; add the project's own required form next to them.
   An approved draft: add only what these rules require (the disclosure
   line, the Related line, the footer), say what you added, and post it; anything more, even a useful fact, changes what the user
   approved and needs a new go.
6. Save the draft to a file, show it, wait. Changes go into the file,
   rendered and shown again.
7. The machine account from the user's instructions (`~/.claude/CLAUDE.md`,
   `AGENTS.md`), per command; the same works for `gh issue comment` and
   `gh pr comment`:

   ```bash
   GH_TOKEN=$(gh auth token --user MACHINE_ACCOUNT) \
     gh issue create -R OWNER/REPO --title "TITLE" --body-file draft-body.md
   ```

   Never `gh auth switch`, never print a token. No machine account: post as
   the user only if they say so. Then `gh issue view N -R OWNER/REPO --json
   author,state,body` (a comment: `gh api repos/OWNER/REPO/issues/comments/ID
   --jq .user.login`) and `gh auth status`. Unbind the PR if your harness
   watches it; report the URL and record it where the work lives.
8. Fix the user's copy, with a test. Then the rungs, each that applies,
   cross-linked: (1) the issue with the diff inline; (2) a PR, even one that
   may not merge (`fetchpatch` can consume it); (3) a fork under the user's
   account as a patch series on the upstream tag, never a divergent tree;
   (4) a deterministic build in that fork (a flake fetching upstream by hash
   and applying the series), never a gist; (5) a distribution patch. Usual
   shape: issue first so the number exists, then the PR with "Fixes #N",
   each linking the other. Ask before a PR whether the user will carry it;
   if not, say so in the issue ("I cannot carry this as a PR; the diff is
   above for whoever can"). A dormant project gets the issue and the fork.
   A PR: fork, topic branch, explicit paths (`git commit --only <paths>` in a
   shared tree, never `git add -A`), the project's commit style and exactly
   the trailer its policy requires or forbids (never a `Signed-off-by`: that
   is the human's to add), its draft or WIP convention while not ready, its tests and linters, one
   logical change; the body links the issue, says what was tested, opens
   with the disclosure, and goes out with `GH_TOKEN=… gh pr create`.
