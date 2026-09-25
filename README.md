# skills

Agent skills by PSJ. Each skill lives in `skills/<name>/`, and where it
is a shar, `SHAR.org` is the source and everything beside it is tangled
from it (`./SHAR.org` unpacks and checks; see its Bootstrap chapter).

- `handoff-agentic-shar`: deliver a multi-file, reproducible program as
  one self-unpacking Org file that any agent can carry forward.
- `upstream-issue`: report to someone else's project as an AI agent (an
  issue, a comment or test report, a patch): read their rules and AI policy,
  search for duplicates, pin verified evidence, disclose the agent on the
  first line, post only on the human's go and from the account they keep for
  agents. Evaluated with Claude Code, Codex and Cursor in `evals/`.

What a receiver needs, measured on `handoff-agentic-shar` in September 2026:

- **Asked to use it, any agent tested manages.** Sonnet 5, Opus 5.5 and
  Codex CLI unpack, check and report. So does Haiku 4.5 on claude.ai with
  thinking off, from an attached file or a raw paste, and it hands a
  change back as a shar that still passes its tests.
- **Dropped with no request, it is described, not run.** That is the
  floor, and it is meant to be: the note inside a shar tells the receiver
  how to run it, and a file's own words are not a request to.

Install with the [skills](https://skills.sh) CLI, which puts them where
Claude Code, Codex, Cursor and other agents look:

```bash
npx skills add psjg/skills
```

License: Reciprocal Public License 1.5 (`LICENSE`), unless a skill says otherwise.
