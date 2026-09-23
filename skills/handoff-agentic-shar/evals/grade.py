#!/usr/bin/env python3
"""Hard-gate grader for the handoff-agentic-shar evals.

Usage: python3 grade.py <iteration-dir>

For every <eval>/<config>/outputs/ under the iteration it checks the assertions
below by *running* the deliverable (tangle twice, Org agree) rather than by
reading the transcript, and writes <eval>/<config>/grading.json in the shape
the skill-creator viewer expects (expectations[{text,passed,evidence}] +
summary).  Assertions are outcome-shaped so a baseline run can pass them
without knowing the skill; the ones only a shar can satisfy are the point.
"""
import json, os, re, shutil, subprocess, sys, tempfile

ATTRIB = "Built with the handoff-agentic-shar skill by PSJ (https://github.com/psjg), licensed under the Reciprocal Public License 1.5."
NOTRUN = re.compile(r"(not run|could not (run|verify|execute|test)|couldn't (run|verify)|did not run|unverified|not verified|not executed|were not|was not run|untested|no checks)", re.I)


def sh(cmd, cwd, timeout=300):
    p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)[-2000:]


def tangle_in_clean(deliv):
    """Copy the deliverable alone into a temp dir, tangle twice, return (files, same, dir)."""
    d = tempfile.mkdtemp(prefix="grade-")
    shutil.copy(deliv, os.path.join(d, "SHAR.org"))
    rc, out = sh("sh SHAR.org tangle", d)
    files1 = sorted(p for p in walk(d) if p != "SHAR.org")
    snap = {p: open(os.path.join(d, p), "rb").read() for p in files1}
    rc2, out2 = sh("sh SHAR.org tangle", d)
    files2 = sorted(p for p in walk(d) if p != "SHAR.org")
    same = files1 == files2 and all(open(os.path.join(d, p), "rb").read() == snap[p] for p in files1)
    return rc, files1, same, d, out


def walk(d):
    for root, _, fs in os.walk(d):
        for f in fs:
            yield os.path.relpath(os.path.join(root, f), d)


def longest_backtick_run(s):
    return max((len(m) for m in re.findall(r"`+", s)), default=0)


def fence_rule(reply):
    """Every fenced block's fence is longer than any backtick run inside it."""
    fences = list(re.finditer(r"^(`{3,})[^\n]*\n(.*?)^\1\s*$", reply, re.S | re.M))
    if not fences:
        return False, "no fenced block in REPLY.md"
    for m in fences:
        if longest_backtick_run(m.group(2)) >= len(m.group(1)):
            return False, f"fence of {len(m.group(1))} backticks contains a run of {longest_backtick_run(m.group(2))}"
    return True, f"{len(fences)} fence(s), each longer than its longest inner backtick run"


def grade(run_dir, eval_name):
    out = os.path.join(run_dir, "outputs")
    E = []
    def add(text, passed, evidence):
        E.append({"text": text, "passed": bool(passed), "evidence": evidence})

    files = sorted(p for p in walk(out) if p != "REPLY.md" and not p.startswith(".")) if os.path.isdir(out) else []
    deliv = [f for f in files if not f.endswith((".json", ".md")) or f.endswith(".org")]
    add("Exactly one deliverable file is handed over (REPLY.md aside)", len(files) == 1, f"outputs: {files}")
    reply = open(os.path.join(out, "REPLY.md")).read() if os.path.exists(os.path.join(out, "REPLY.md")) else ""

    one = os.path.join(out, files[0]) if len(files) >= 1 else None
    cand = [f for f in files if f.endswith(".org")] or files
    one = os.path.join(out, cand[0]) if cand else None
    text = open(one, errors="replace").read() if one else ""

    add("The deliverable keeps the License notice attribution line verbatim", ATTRIB in text, "found" if ATTRIB in text else "attribution line absent")

    extracted, same, tdir, tout = [], False, None, ""
    if one and text.startswith("#!/bin/sh"):
        rc, extracted, same, tdir, tout = tangle_in_clean(one)
        add("Running the deliverable alone in a clean directory extracts at least two files, twice identically",
            rc == 0 and len(extracted) >= 2 and same, f"rc={rc} files={extracted} idempotent={same}")
        add("A flake.nix is among the extracted files, so Nix alone reproduces the toolchain", "flake.nix" in extracted, f"extracted={extracted}")
        rc_a, out_a = sh("sh SHAR.org agree", tdir, timeout=600)
        add("Org's own tangle agrees byte for byte with the deliverable's tangler (TANGLERS-AGREE)", "TANGLERS-AGREE" in out_a, out_a.strip().splitlines()[-1] if out_a.strip() else f"rc={rc_a}")
    else:
        add("Running the deliverable alone in a clean directory extracts at least two files, twice identically", False, "deliverable is not a self-extracting sh file")
        add("A flake.nix is among the extracted files, so Nix alone reproduces the toolchain", "flake.nix" in files, f"files={files}")
        add("Org's own tangle agrees byte for byte with the deliverable's tangler (TANGLERS-AGREE)", False, "nothing to tangle")

    add("The deliverable states what is not derived from it (lockfiles, results)", bool(re.search(r"not derived", text, re.I)), "phrase 'not derived' " + ("present" if re.search(r"not derived", text, re.I) else "absent"))

    if eval_name == "tla-model-to-colleague":
        add("Extracted files include a .tla spec and a .cfg for TLC", any(f.endswith(".tla") for f in extracted) and any(f.endswith(".cfg") for f in extracted), f"extracted={extracted}")
        add("A README is extracted that names what is unchecked", any(f.lower().startswith("readme") for f in extracted) and bool(re.search(r"uncheck|not (yet )?(checked|verified|proved)", text, re.I)), f"extracted={extracted}")
    if eval_name == "python-tool-via-slack":
        ok, ev = fence_rule(reply)
        add("The reply pastes the deliverable in a fence longer than any backtick run inside it", ok, ev)
        add("Extracted files include the CLI and a pytest test", any("test" in f for f in extracted) and any(f.endswith(".py") and "test" not in f for f in extracted), f"extracted={extracted}")
    if eval_name == "text-only-sandbox":
        add("The reply says plainly which checks were not run", bool(NOTRUN.search(reply)), (NOTRUN.search(reply).group(0) if NOTRUN.search(reply) else "no such statement in REPLY.md"))
        add("Extracted files include an awk script and a sh wrapper", any(f.endswith(".awk") for f in extracted) and any(f.endswith(".sh") for f in extracted), f"extracted={extracted}")

    if tdir: shutil.rmtree(tdir, ignore_errors=True)
    passed = sum(e["passed"] for e in E)
    g = {"expectations": E, "summary": {"passed": passed, "failed": len(E) - passed, "total": len(E), "pass_rate": round(passed / len(E), 2)}}
    tp = os.path.join(run_dir, "timing.json")
    if os.path.exists(tp): g["timing"] = json.load(open(tp))
    json.dump(g, open(os.path.join(run_dir, "grading.json"), "w"), indent=2)
    return g


if __name__ == "__main__":
    it = sys.argv[1]
    for ev in sorted(os.listdir(it)):
        ed = os.path.join(it, ev)
        if not os.path.isdir(ed): continue
        for cfg in ("with_skill", "without_skill", "old_skill"):
            rd = os.path.join(ed, cfg)
            if os.path.isdir(rd):
                g = grade(rd, ev)
                print(f"{ev:28s} {cfg:14s} {g['summary']['passed']}/{g['summary']['total']}")
