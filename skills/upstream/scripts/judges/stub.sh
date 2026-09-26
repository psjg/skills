#!/bin/sh
# stub.sh: the --judge-cmd contract with no judgement in it. Reads the JSON
# request on stdin, answers that nothing blocks. For evals and installs
# without a second model family; STATE.json records which judge ran.
cat >/dev/null
echo '{"findings": []}'
