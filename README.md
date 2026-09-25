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

Install with the [skills](https://skills.sh) CLI, which puts them where
Claude Code, Codex, Cursor and other agents look:

```bash
npx skills add psjg/skills
```

License: Reciprocal Public License 1.5 (`LICENSE`), unless a skill says otherwise.
