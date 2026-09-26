#!/usr/bin/env python3
"""verdict.py -- the shared half of the judge adapters in this directory.

    verdict.py prompt  < request.json   > prompt.txt    the text a model reads
    verdict.py extract < model-output   > verdict.json  always a valid verdict

``prompt`` turns upstream.py's JSON request (instructions, draft, ledger,
flagged sentences) into one plain-text prompt. ``extract`` finds the first
balanced JSON object carrying a ``findings`` list in whatever the model
printed, and fails closed: no such object is one blocking finding, "judge
output unparseable". Both reuse upstream.py's own definitions, so the
adapters and the script can not disagree about what a verdict is.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from upstream import parse_verdict  # noqa: E402

if sys.argv[1:] == ["prompt"]:
    req = json.load(sys.stdin)
    print(req["instructions"], "\nDo not run tools or edit files; answer from the text below only.\n",
          "CONTRACT LINES (required boilerplate: never judge or quote these):\n"
          + json.dumps(req.get("contract_lines", []), indent=1),
          "DRAFT:\n" + req["draft"], "LEDGER (claims with their commands and captured output):\n"
          + json.dumps(req["ledger"], indent=1), "FLAGGED:\n" + json.dumps(req["flagged"], indent=1), sep="\n")
elif sys.argv[1:] == ["extract"]:
    print(json.dumps({"findings": parse_verdict(sys.stdin.read())}))
else:
    sys.exit("usage: verdict.py prompt|extract")
