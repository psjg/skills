#!/bin/sh
# cursor.sh: --judge-cmd adapter for cursor-agent with Grok, invoked as the
# evals' run.sh invokes it. JSON request on stdin, JSON verdict on stdout.
# --force lets it run without prompts; it works in an empty temp dir, and the
# prompt tells it to use no tools. Its text output is searched for the
# verdict object; none found is a blocking finding (verdict.py).
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/judge-cursor.XXXXXX") && trap 'rm -rf "$tmp"' EXIT
python3 "$here/verdict.py" prompt > "$tmp/prompt" || exit 1
cursor-agent -p --force --model "${JUDGE_CURSOR_MODEL:-grok-4.7-medium}" --output-format text --workspace "$tmp" \
  "$(cat "$tmp/prompt")" < /dev/null 2>&1 | python3 "$here/verdict.py" extract
