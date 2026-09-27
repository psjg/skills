#!/bin/sh
# file.sh: --judge-cmd adapter for a judge reached outside the shell (an
# MCP tool, a web UI). Pass 1 (no JUDGE_VERDICT): saves the JSON request
# to $JUDGE_REQUEST and the rendered prompt to $JUDGE_REQUEST.prompt, then
# emits nothing, which verdict.py turns into a blocking "unparseable"
# finding. Pass 2 (JUDGE_VERDICT set to a file holding the judge's raw
# reply): emits that reply through the same extractor as the CLI adapters.
here=$(cd "$(dirname "$0")" && pwd)
req=${JUDGE_REQUEST:?set JUDGE_REQUEST to a path}
if [ -n "${JUDGE_VERDICT:-}" ]; then
  python3 "$here/verdict.py" extract < "$JUDGE_VERDICT"; exit
fi
cat > "$req"
python3 "$here/verdict.py" prompt < "$req" > "$req.prompt"
