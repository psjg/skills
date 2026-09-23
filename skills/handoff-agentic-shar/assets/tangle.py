#!/usr/bin/env python3
"""Tangle SHAR.org without sh or awk: python3 tangle.py SHAR.org
Mirrors the awk tangler in the preamble exactly: :tangle on a block or
inherited from a heading's :header-args:LANG:, :tangle no, mkdir, comma
escapes, trailing blank lines dropped, blocks of one file joined by one blank."""
import os, re, sys

def target(header):
    w = header.split()
    for i, x in enumerate(w):
        if x == ":tangle" and i + 1 < len(w):
            return w[i + 1]
    return ""

def tangle(path):
    section = ""; out = None; buf = []; first = {}; inblock = False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("* "):
                section = ""
            elif re.match(r"^:header-args:[a-z]*: ", line):
                t = target(line)
                if t: section = t
            elif line.startswith("#+begin_src"):
                t = target(line) or section
                if t in ("", "no"):
                    out = None; continue
                out = t; buf = []; inblock = True
                d = os.path.dirname(t)
                if d: os.makedirs(d, exist_ok=True)
                if out not in first:
                    open(out, "w").close(); first[out] = True
            elif line.startswith("#+end_src"):
                if inblock and out:
                    while buf and buf[-1] == "": buf.pop()
                    with open(out, "a", encoding="utf-8") as o:
                        if not first[out]: o.write("\n")
                        for b in buf: o.write(b + "\n")
                    first[out] = False
                inblock = False; out = None
            elif inblock and out:
                if re.match(r"^,(\*|#\+)", line): line = line[1:]
                buf.append(line)

if __name__ == "__main__":
    tangle(sys.argv[1] if len(sys.argv) > 1 else "SHAR.org")
