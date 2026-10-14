#!/usr/bin/env bash
# Reproduces the committed evaluation runs (results + recordings). Needs:
#   - a Temporal server on $TEMPORAL_ADDRESS (default localhost:7233):  temporal server start-dev
#   - Ollama with the models below pulled:  ollama pull qwen2.5:3b-instruct qwen2.5-coder:3b qwen2.5-coder:7b
#   - optional Langfuse (LANGFUSE_* in .env) to also get traces and scores
# Every generation is recorded to evals/recordings/<run>.json so CI can replay it without a model.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
ADP=${ADP:-.venv/bin/adp}

run() {  # name variants spec_model code_model review_model
  local name=$1 variants=$2
  export ADP_SPEC_MODEL=$3 ADP_CODE_MODEL=$4 ADP_REVIEW_MODEL=$5
  export ADP_RECORD_TO="evals/recordings/$name.json" ADP_OUTPUT_DIR="runs/$name"
  export ADP_TASK_QUEUE="adp-eval-$name-$$"        # a private queue: no other worker can pick up this run's tasks
  rm -f "$ADP_RECORD_TO" "$ADP_RECORD_TO.lock"
  $ADP worker >"/tmp/adp-worker-$name.log" 2>&1 &
  local worker=$!
  trap 'kill "$worker" 2>/dev/null || true' EXIT
  sleep 5
  $ADP eval --variants "$variants" --out "evals/results/$name.json"
  kill "$worker" 2>/dev/null || true
  wait "$worker" 2>/dev/null || true
  trap - EXIT
}

run qwen2.5-3b v1-naive,v2-spec-driven qwen2.5:3b-instruct qwen2.5-coder:3b qwen2.5:3b-instruct
# Optional larger-model run (needs `ollama pull qwen2.5-coder:7b`, ~5 GB of RAM; slow on a shared laptop). It was started once
# and cancelled because it made the machine unusable, so no 7B result is committed.
# run qwen2.5-coder-7b v2-spec-driven qwen2.5-coder:7b qwen2.5-coder:7b qwen2.5-coder:7b
