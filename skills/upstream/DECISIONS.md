# Decisions

Append-only. Each entry is a decision that is hard to reverse, with the
measurement behind it.

## D1. The flow is a Python state machine, not a build system (2026-09-26)

The gates read like a build: "this artefact exists", "the draft's hash has
not changed since approval". So both GNU make and djb redo (apenwarr
0.42d) were tried as a replacement for `scripts/upstream.py`, each in its
own branch with the same acceptance: net diff negative including the
ledger split, smaller surface, tests green. Both failed the diff gate.

| | state machine | make | redo |
|---|---|---|---|
| code + tests, lines | 1840 | 1862 | 1954 |
| commands / flags | 17 / 36 | 18 / 39 | 13 / 30 |
| tests | 33 in 59 s | 34 in 143 s | 38 in 168 s |
| invalidation | sha256 | mtime, whole seconds on Apple's make 3.81 | mtime µs + size + inode |
| needs | gh, git, python | same | plus redo |

Why: the automaton proper is about 160 of the 1192 lines. The rest is
plumbing (gh, the draft checker, the judge adapters, the fork rung) and
four guards that are predicates, not file tests: every hit marked; draft
sha plus inputs sha; blocking findings minus overrides; an approval that
survives the machine-added disclosure, Related and footer lines. A build
tool cannot express those, and it needs the one ledger split into files,
which costs the lines the graph saved. Build tools key on time; the
invariant here is content: Apple's make missed a prerequisite 70 ms
newer than its target, so every stamp had to be backdated a second.

Kept: the state machine. The graph stays data in `STATES`/`EDGES`, from
which `graph --mermaid` is emitted. Measure where the lines are before
choosing a formalism for the part that looks formal.
