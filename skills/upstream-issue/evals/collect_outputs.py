"""Gather what one eval run produced into RUN_DIR/outputs/ for the viewer.

    python3 collect_outputs.py RUN_DIR

The skill-creator viewer shows RUN_DIR/outputs/ only, but the agent works in
RUN_DIR/work/, next to the fixture and the harness plumbing. This copies what
a reviewer needs and nothing else:

- files the agent wrote at the top of work/ and under work/submission/
  (the harness's own AGENTS.md, CLAUDE.md, gh.log and dot-dirs excluded);
- files the agent changed or added in the fixture clone, per `git status`;
- gh-calls.txt: every call the fake gh saw, with the account it ran as;
- posted-body.md: the body of the last issue or PR the agent created;
- final-message.txt: the tail of the agent's transcript.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

HARNESS = {"AGENTS.md", "CLAUDE.md", "gh.log"}


def collect(run: Path) -> list[str]:
    work, out = run / "work", run / "outputs"
    out.mkdir(exist_ok=True)
    copied = []
    for p in list(work.glob("*")) + list(work.glob("submission/**/*")):
        if p.is_file() and p.name not in HARNESS and not p.name.startswith("."):
            name = str(p.relative_to(work)).replace("/", "--")
            shutil.copy(p, out / name)
            copied.append(name)
    status = subprocess.run(["git", "-C", str(work / "tinyparse"), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True).stdout
    for line in status.splitlines():
        rel = line[3:]
        src = work / "tinyparse" / rel
        if src.is_file() and "__pycache__" not in rel:
            name = "tinyparse--" + rel.replace("/", "--")
            shutil.copy(src, out / name)
            copied.append(name)
    log = work / "gh.log"
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    (out / "gh-calls.txt").write_text("".join(f"[{c['as']}] gh {' '.join(c['argv'])}\n" for c in calls) or "no gh calls\n")
    bodies = [c["body"] for c in calls if c["argv"][1:2] == ["create"] and c.get("body")]
    if bodies:
        (out / "posted-body.md").write_text(bodies[-1])
    transcript = run / "transcript.txt"
    if transcript.exists():
        (out / "final-message.txt").write_text(transcript.read_text(errors="replace")[-4000:])
    return copied


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(arg, collect(Path(arg)))
