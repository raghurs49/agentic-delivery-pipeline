# Agentic Delivery Pipeline

[![ci](https://github.com/raghurs49/agentic-delivery-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/raghurs49/agentic-delivery-pipeline/actions/workflows/ci.yml)

Turn a requirement into reviewed, tested code with AI agents **and human checkpoints**: orchestrated with **Temporal**, traced
and scored in **Langfuse**, and evaluated against **hidden-test ground truth** so the quality gate itself is measured.

Runs entirely on free, local models (Ollama). No paid API is required.

```
requirement -> spec -> [human approves] -> tests (from the spec, before any code) -> implement <-> repair
            -> sandboxed test run -> review -> quality gate -> artifacts          (every step traced in Langfuse)
```

## What is in here

| Area | What it shows |
|---|---|
| **Orchestration** (`workflows.py`, `activities.py`) | Durable workflow with a human-approval **signal** and timeout, a status **query**, activity retries with backoff and heartbeats, **non-retryable** errors, cancellation that still records an audit trail, child workflows for batch evaluation |
| **Spec-driven development** (`agents.py`) | Requirement -> structured spec (signature, acceptance criteria, edge cases) -> tests written *from the spec before the code exists*, so the agent cannot tailor tests to its own mistakes |
| **Guardrails and quality gate** (`guardrails.py`, `gate.py`, `sandbox.py`) | Forbidden imports/calls, test-tampering patterns, secret scanning, diff-size cap, vacuous-test detection; a gate that is a pure, unit-tested function; sandboxed test execution with timeouts |
| **Observability** (`observability.py`) | One Langfuse trace per workflow (id derived from the workflow id), a generation per LLM call with token usage, a span per step, outcome **scores** on the trace |
| **Evaluation** (`evals.py`, `eval_runner.py`, `tasks/`) | 12 tasks with reference solutions and hidden tests; metrics include gate precision, **false accepts** and false rejects; regression comparison against a committed baseline |
| **CI** (`.github/workflows/ci.yml`, `tests/test_eval_replay.py`) | A deterministic **replay** of recorded model generations through the real pipeline; any change to the gate, guardrails, parsing or repair logic that changes outcomes fails the build |

Design decisions, failure semantics and limits are in [docs/DESIGN.md](docs/DESIGN.md).

## Quick start (all local, no cost)

Requires Python 3.10+, the [Temporal CLI](https://docs.temporal.io/cli), and [Ollama](https://ollama.com).

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
ollama pull qwen2.5-coder:3b && ollama pull qwen2.5:3b-instruct

temporal server start-dev                  # terminal 1 (UI on http://localhost:8233)
.venv/bin/adp worker                       # terminal 2

# terminal 3: deliver a task with a human checkpoint
.venv/bin/adp run roman_to_int             # waits at "awaiting_approval"
.venv/bin/adp status <workflow-id>         # shows the generated spec
.venv/bin/adp approve <workflow-id>        # or: --reject --comment "why"

# or run unattended
.venv/bin/adp run is_balanced --auto-approve --variant v2-spec-driven
```

Artifacts (`solution.py`, `test_solution.py`, `spec.json`, `result.json`, `report.md`) are written to `runs/<task>/<workflow-id>/`.

### Optional: Langfuse tracing (self-hosted)

```bash
# Langfuse's own compose file (kept outside this repo); bind ports to localhost and set real secrets.
curl -O https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml
docker compose up -d
cp .env.example .env     # add the project keys from Langfuse (or Langfuse Cloud's free tier)
```

With `LANGFUSE_*` set, each run produces one trace; without it a no-op tracer is used and nothing else changes.
The code targets **Langfuse Python SDK v4**.

## Evaluation

Reproduce with `scripts/run_evals.sh` (needs a Temporal server, Ollama and optionally Langfuse). Every generation is recorded, so
results can be replayed in CI without a model. `scripts/make_report.py --update-readme` regenerates the table below from
`evals/results/*.json`.

<!-- results:start -->

| Model | Variant | Hidden tests pass | 95% CI | Gate accepted | Gate precision | False accepts | False rejects | Avg repairs | LLM time / task |
|---|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder:3b | v1-naive | 6/12 (50%) | 25%-75% | 1/12 | 100% | 0 | 5 | 0.0 | 43.8s |
| qwen2.5-coder:3b | v2-spec-driven | 6/12 (50%) | 25%-75% | 3/12 | 100% | 0 | 3 | 1.33 | 60.9s |

Outcome mix per run (status counts):

- `qwen2.5-coder:3b` / `v1-naive`: delivered 1, failed 1, rejected_by_gate 10
- `qwen2.5-coder:3b` / `v2-spec-driven`: delivered 3, failed 1, rejected_by_gate 8

<!-- results:end -->

### Findings

<!-- findings:start -->

Run: `qwen2.5-coder:3b` writes tests and code, `qwen2.5:3b-instruct` writes specs and reviews; 12 tasks; temperature 0.
(A larger 7B run was started and cancelled because it made the laptop unusable; no 7B result is committed.)

1. **The gate never let wrong code through (0 false accepts in 24 task runs) but was conservative.** Of the 6 tasks where the final
   code passed the hidden tests, the naive variant delivered 1 and the spec-driven variant delivered 3. The rest were rejected
   because the *model-written tests* were weak or wrong, not because the code was wrong. With small models, **test quality is the
   bottleneck**, which is why generated tests go through their own quality check and are regenerated with feedback.
2. **Same number of correct solutions, different ones.** Both variants produced correct code for 6 of 12 tasks (95% interval about
   25-75%), but not the same six. The spec-driven variant fixed `prorated_amount` and the gate accepted three of its correct
   solutions, yet it also broke `roman_to_int`: after two repair rounds against a flawed generated test, working code became wrong.
   **A repair loop chases whatever the tests say.** Only hidden-test ground truth shows this; the pipeline's own tests could not.
3. **The spec-driven variant costs more:** about 56% more tokens and 39% more model time per task. On 12 tasks that did not buy
   more correct code, only more accepted code. With this sample size I would not claim it is better, only that it changes where the
   failures are.
4. **Failures are mostly model limits, and they are visible:** one task failed at spec generation (the model's JSON stayed invalid
   even after the error was fed back), the other variant produced a syntax error that the guardrail caught and the repair loop could not fix.
5. **Real-model runs exposed pipeline bugs that fake-model tests could not**, each now fixed and covered by a test: temperature-0 retries
   repeating the same invalid reply (now error feedback), sandbox output containing temp paths and timings (made replay impossible),
   a macOS `/var` vs `/private/var` scrubbing-order bug, two recorders overwriting one file, and Temporal client long-poll timeouts.

<!-- findings:end -->

### How to read this honestly

* **12 tasks is a small sample.** The Wilson intervals are wide; differences between variants are directions, not proof.
* **The models are small and local.** Absolute pass rates will be much higher with a stronger model; the pipeline and the
  evaluation method are what transfer. The provider interface (`llm.py`) is the extension point.
* **"Hidden tests" are my own.** They are verified by `tests/test_tasks_meta.py` (each suite passes its reference solution and
  fails a stub), but they are not an independent benchmark.
* **Gate precision is a property of this gate on these tasks**, not a general claim about LLM code review.

## Tests

```bash
.venv/bin/python -m pytest                  # unit, Temporal workflow scenarios, tests-for-the-tests, replay regression
```

| Layer | What it proves |
|---|---|
| Unit (`test_guardrails`, `test_gate`, `test_sandbox`, `test_agents`, `test_evals`) | Pure logic: guardrail rules, gate decisions, sandbox timeouts and output scrubbing, parsing of messy model output, metrics and regression detection |
| Workflow (`test_workflow`) | Real Temporal server, scripted fake model: happy path, human approval/edit/reject/timeout, bounded repair loop, guardrail blocking, non-retryable vs retried failures, test regeneration with feedback, cancellation audit trail, batch evaluation |
| Tests-for-the-tests (`test_tasks_meta`) | Every hidden suite accepts its reference solution and rejects a stub |
| Replay regression (`test_eval_replay`) | Recorded real-model generations replayed through the real workflow reproduce the committed outcomes task by task |
| Langfuse integration (`test_langfuse_integration`) | A run produces spans, generations with token usage, the session id on every observation, and scores (skipped unless a Langfuse server is configured) |

## Status and limits

A portfolio project built to explore agentic delivery workflows end to end. It is **not** a production system: no Jira/Linear
intake, no repository checkout or pull-request delivery, no auth on the approval signal, and the sandbox is a best-effort
guard (not a security boundary). See [docs/DESIGN.md](docs/DESIGN.md) for the roadmap.
