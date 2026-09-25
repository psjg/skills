#!/bin/sh
# run.sh AGENT CONDITION EVAL_ID OUTDIR
#   AGENT: claude | codex | cursor     CONDITION: with_skill | without_skill
# Builds a fresh fixture in OUTDIR/work, runs the agent there with the fake gh
# first on PATH, and keeps the transcript. with_skill copies SKILL.md into the
# work dir and tells the agent to read it; without_skill gets the bare prompt.
set -eu
agent=$1 cond=$2 id=$3 out=$4
here=$(cd "$(dirname "$0")" && pwd)
codex_home=${CODEX_HOME:-$HOME/.codex}  # read before any HOME change
spec=$(python3 -c "import json,sys; e=[e for e in json.load(open('$here/evals.json'))['evals'] if e['id']==$id][0]; print(e['policy']); print('with-draft' if e.get('with_draft') else '-'); print(e['prompt'])")
policy=$(echo "$spec" | sed -n 1p); draft=$(echo "$spec" | sed -n 2p); prompt=$(echo "$spec" | sed -n '3,$p')
rm -rf "$out"; mkdir -p "$out"
work=$out/work
sh "$here/make_fixture.sh" "$work" "$policy" "$([ "$draft" = with-draft ] && echo with-draft)"
if [ "$cond" = with_skill ]; then
  mkdir -p "$work/.skill/upstream-issue" && cp "$here/../SKILL.md" "$work/.skill/upstream-issue/SKILL.md"
  prompt="Read the skill at .skill/upstream-issue/SKILL.md and follow it for this task.

$prompt"
fi
export PATH="$work/bin:$PATH" GHSHIM_LOG="$work/gh.log"
unset GH_TOKEN GITHUB_TOKEN
# Seal against the real gh, which an agent can still reach (Codex's login
# shell resets PATH): no config, a host that does not exist, and a fixture
# repo in the user's own namespace that does not exist, so even a real token
# explicitly fetched for github.com can post nowhere.
mkdir -p "$work/.gh-empty"
export GH_CONFIG_DIR="$work/.gh-empty" GH_HOST=github.invalid
cd "$work"
start=$(date +%s)
case $agent in
claude) timeout 900 claude -p "$prompt" --model claude-sonnet-5 --setting-sources project --permission-mode bypassPermissions --output-format text > "$out/transcript.txt" 2>&1 || true ;;
codex)  # Codex runs each command in a login shell that rebuilds PATH from the
        # profile, hiding the fake gh; a scratch HOME whose profile puts it first
        # fixes that, and CODEX_HOME keeps Codex's own login.
        mkdir -p "$work/.home"
        for rc in .zshenv .bash_profile .profile; do echo "export PATH=\"$work/bin:\$PATH\"" > "$work/.home/$rc"; done
        HOME="$work/.home" CODEX_HOME="$codex_home" timeout 900 codex exec --skip-git-repo-check -s workspace-write -c sandbox_workspace_write.network_access=false -c approval_policy=never -C "$work" -o "$out/final.txt" "$prompt" < /dev/null > "$out/transcript.txt" 2>&1 || true ;;
cursor) timeout 900 cursor-agent -p --force --model grok-4.7-medium --output-format text --workspace "$work" "$prompt" < /dev/null > "$out/transcript.txt" 2>&1 || true ;;
esac
echo "{\"agent\": \"$agent\", \"condition\": \"$cond\", \"eval_id\": $id, \"seconds\": $(( $(date +%s) - start ))}" > "$out/run.json"
