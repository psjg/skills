#!/bin/sh
# codex.sh: --judge-cmd adapter for OpenAI's codex CLI (a model family other
# than the drafting agent's). JSON request on stdin, JSON verdict on stdout.
# Runs read-only in an empty temp dir and reads only codex's last message
# (-o). A failed or quota-limited run leaves no verdict, which verdict.py
# turns into a blocking "judge output unparseable" finding.
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/judge-codex.XXXXXX") && trap 'rm -rf "$tmp"' EXIT
python3 "$here/verdict.py" prompt > "$tmp/prompt" || exit 1
codex exec --skip-git-repo-check -s read-only -C "$tmp" -o "$tmp/last" - < "$tmp/prompt" > "$tmp/log" 2>&1
{ cat "$tmp/last" 2>/dev/null || tail -c 300 "$tmp/log"; } | python3 "$here/verdict.py" extract
