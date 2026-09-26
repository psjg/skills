# upstream

An agent that reports to someone else's project: an issue, a comment or
test report on an existing thread, a patch as a PR or as a fork, and the
links between them.

## Why it exists

A contribution is worth the maintainer minutes it returns minus the
minutes it costs them to check. A maintainer who receives a plausible
story has to do the checking the author skipped; that cost, not the fact
that a model wrote it, is what projects mean by slop, and it is why a
growing number of them refuse AI content outright. An agent can do the
opposite: spend its own tokens before it spends anyone's minutes. Every
claim in the posted text is a command the maintainer can paste, with its
output and a commit-pinned link. What could not be run is left out, not
hedged.

The skill grew out of posting a test report on a Mozart 2 PR and an
LP64 port of Mozart 1.4 from a machine account. Two things were missing
from the classic etiquette texts (Tatham, Raymond and Moen, Fogel, the
kernel's *Submitting patches*): disclosure, because a reader decides how
to read the rest from the first line; and a hard gate on posting, because
"the user said file issues" is not a yes to this text.

## What it does

`scripts/upstream.py` is the skill as an executable state machine. Each
step is a state, and the script refuses every transition whose artefacts
do not exist yet:

1. **House rules.** Fetch the project's contribution and AI policy and
   classify it per channel: welcome, disclose and attest, issue only until
   trust is earned, refuse. The disclosure form is theirs (a trailer, a
   sentence, nothing).
2. **Search.** Issues and PRs, open and closed, every hit marked related,
   unrelated or duplicate. A duplicate becomes a comment there.
3. **Evidence.** A claim ledger: each sentence as it will be posted, the
   command that shows it, its output, a permalink. `draft check` re-runs
   every command against the draft.
4. **Draft.** Disclosure first (model, harness, reasoning effort, the
   accountable human's real handle), five visible lines, the rest in
   `<details>`, the fix inline as a diff, a Related line to every
   neighbouring issue, PR and fork, a footer naming this skill at a pinned
   commit.
5. **Judge.** A decision model checks the draft against the ledger; then
   an adversarial LLM from another model family reads only the sentences
   and the ledger. An unparseable verdict blocks.
6. **The go.** The user's explicit yes to this exact text, recorded with
   the draft's hash. An edit after approval sends the draft back through
   check, judge and approval.
7. **Post.** From the machine account, token per command, author
   verified, URL recorded.
8. **Fixes and rungs.** Issue and PR together, with the diff in both,
   where PRs are welcome; the diff in the issue where PRs are refused; a
   patch series on the upstream tag in a fork under the machine account
   where the project refuses AI content or the series is too large to
   attest. Never a gist: a fork stays findable and pinnable.

The script is the only route to the forge. On the author's machines a
PreToolUse hook denies `gh issue create`, `gh pr create`, `gh issue
comment` and `gh repo fork` typed by hand, so the gate is hard rather
than a request.

## Layout

- `SKILL.md`: What (the flow, generated from the script), Why (one
  paragraph per step), How (the steps as commands).
- `scripts/upstream.py`, `scripts/test_upstream.py`, `scripts/judges/`:
  the state machine, its tests, and the judge adapters (Codex, Cursor, a
  stub that fails closed).
- `references/examples.md`: worked cases. `references/sources.md`: the
  project policies and etiquette texts behind step 1.
- `evals/`: seven scenarios (a trailing field, an approved draft, a
  no-AI policy, a mailing list, a closed won't-fix, a no-mentions rule),
  graded mechanically, run against Claude Code, Codex and Cursor.

## Results

Seven evals, three runs each, Sonnet 5 as the agent: the script-driven
skill scores 153/153; the text-only version 151/153. Every post in the
evals came from the script under the machine account; every `gh` call
outside it was a read.
