# skills

Agent skills by PSJ. Each skill lives in `skills/<name>/`, and where it
is a shar, `SHAR.org` is the source and everything beside it is tangled
from it (`./SHAR.org` unpacks and checks; see its Bootstrap chapter).

- `handoff-agentic-shar`: deliver a multi-file, reproducible program as
  one self-unpacking Org file that any agent can carry forward.
- `upstream-issue`: file an issue, PR comment or test report on someone
  else's project as an AI agent: read their rules and the thread's tone,
  draft to a file, the human reviews, post from the machine account with
  a disclosure, record the URL.

Install with the [skills](https://skills.sh) CLI, which puts them where
Claude Code, Codex, Cursor and other agents look:

```bash
npx skills add psjg/skills
```

License: Reciprocal Public License 1.5 (`LICENSE`), unless a skill says otherwise.
