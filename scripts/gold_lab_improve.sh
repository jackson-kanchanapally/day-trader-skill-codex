#!/bin/sh
# Monthly gold lab improvement with any server-hosted AI (docs/GOLD_LAB.md#self-improvement).
# Local models (Ollama, LM Studio, localhost) are refused.
# Examples:
#   scripts/gold_lab_improve.sh --agent pi
#   scripts/gold_lab_improve.sh --agent pi --model openai/gpt-6-sol
#   scripts/gold_lab_improve.sh --agent codex
#   scripts/gold_lab_improve.sh --agent claude
#   DEEPSEEK_API_KEY=... scripts/gold_lab_improve.sh --agent api --provider deepseek
#   scripts/gold_lab_improve.sh --agent cmd --agent-cmd "mytool --prompt-file {prompt_file}"
# Work happens on a new branch in an isolated worktree; nothing is merged or pushed.
set -eu
cd "$(dirname "$0")/.."
[ -f config/picks.json ] && set -- --config config/picks.json "$@"
exec python3 -m india.gold_lab_improve "$@"
