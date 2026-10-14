# Design notes

This document explains *why* the pipeline is built the way it is, what it guarantees, and what it deliberately does not.

## Goal

Turn a requirement into reviewed, tested code through AI agents **with human checkpoints**, and be able to answer three
questions about every run and about the pipeline as a whole:

1. What happened, step by step, and what did it cost? (observability)
2. Is the output any good, measured against something the agents never saw? (evaluation)
3. If we change a prompt, model or gate rule, did we make it better or worse? (regression)

## Architecture

```
            +------------------+        signal: approve_spec        +-------------------+
 requirement| DeliveryWorkflow |<------------------------------------|  human (adp CLI)  |
 ---------->|   (Temporal)     |------ query: status -------------->|                   |
            +--------+---------+                                      +-------------------+
                     | activities (retries, timeouts, heartbeats)
   +-----------------+----------------------------------------------------------+
   | generate_spec -> [human approval] -> generate_tests -> implement/repair loop |
   |    -> run_tests (sandbox) -> review -> quality gate -> write_artifacts       |
   |                                                    -> finalize_trace        |
   +-----------------+----------------------------------------------------------+
                     |                                   |
            LLM provider (Ollama,              Langfuse (one trace per workflow;
            replay, fake)                       spans, generations, scores)
```

* **Workflow code is deterministic**; every side effect (LLM, subprocess, files, tracing) is an activity.
* **Tests are written from the spec before any code exists** (`generate_tests` runs before `implement`). An agent that
  writes both can quietly tailor the tests to its own mistakes; here it cannot.
* **The repair loop is bounded** (`Variant.max_repairs`). Each repair sends the failing test output back to the
  implementer. Unbounded loops are how agent pipelines burn money.

## Why Temporal

| Need | Temporal feature used |
|---|---|
| Human approves the spec, possibly hours later | `wait_condition` on a **signal**, with a timeout; the worker can restart meanwhile |
| Inspect a run mid-flight (spec, state) | **query** `status` |
| Flaky model server | activity **retry policy** with exponential backoff and a heartbeat timeout |
| A model that cannot produce valid output | **non-retryable** `ApplicationError` (`InvalidModelOutput`); retrying the same request would not help |
| Abort a run and keep an audit trail | **cancellation** is caught, the outcome is recorded, then re-raised |
| Evaluate many tasks safely in parallel | **child workflows** with a semaphore (`EvalBatchWorkflow`) |
| Idempotent delivery | artifacts are written to a directory keyed by workflow id |

### Failure semantics

| Failure | Behaviour |
|---|---|
| Ollama unreachable / timeout (`LLMError`) | retried by Temporal: 3 attempts, 2 s -> 30 s backoff |
| Model returns invalid JSON / no code block | re-prompted **with the specific error fed back** (a temperature-0 model repeats itself if the prompt is unchanged), then `InvalidModelOutput` (not retried) |
| Reviewer returns no valid score | not an infrastructure error: score 0, the gate rejects |
| No approval within the window | status `approval_timeout`, artifacts and trace still written |
| Approval rejected | status `rejected_by_reviewer`; no code is ever generated |
| Cancelled | status `cancelled`, artifacts and trace written, cancellation propagated |
| Bookkeeping (artifacts/tracing) fails | recorded in `error`; never changes the delivery outcome |

## Guardrails and the quality gate

Guardrails are **pure functions** (`guardrails.py`), unit-tested without a model or a server:

* forbidden imports/calls in generated code (`os`, `subprocess`, `socket`, `eval`, `open`, ...),
* test-tampering patterns (`pytest.skip`, `sys.exit`, `conftest`, monkey-patching builtins, `assert True`),
* secrets in code (AWS keys, private-key blocks, tokens),
* diff-size cap,
* generated **tests** must be real tests: parseable, enough functions and assertions, not vacuous, importing the solution.

The gate (`gate.py`) is also a pure function of the evidence: tests pass, no guardrail violations, review score >= 3.
Every reason for a rejection is reported, not just the first.

**Honest limit:** the sandbox (`sandbox.py`) uses a temp directory, a scrubbed environment, CPU/file rlimits and a killed
process group. That stops accidents, not attackers. For untrusted code in production use containers or gVisor with no
network and a separate identity.

## Observability (Langfuse)

* **One trace per workflow run.** The trace id is derived from the workflow id (`sha256(workflow_id)[:32]`), so every
  activity, including retries, attaches to the same trace without passing state around.
* Each LLM call is a **generation** with model, parameters, input/output and token usage; each step is a **span**.
* The outcome is attached as **scores**: `gate_accepted`, `tests_pass_ratio`, `review_score`, `guardrail_violations`,
  `repair_iterations`, and later (from the evaluation) `hidden_tests_passed`. That last score links ground truth to the
  exact trace that produced the work, so you can filter "gate accepted but hidden tests failed" in the UI.
* Tracing is optional: without Langfuse keys a `NullTracer` is used and behaviour is identical.

## Evaluation

* 12 small tasks, each with a **reference solution** and **hidden tests**. The pipeline never sees the hidden tests.
* **Tests for the tests:** `tests/test_tasks_meta.py` proves every hidden suite passes its reference and fails a stub.
* After a run, the final code is scored against the hidden tests. That measures the *gate*, not just the model:
  precision (of accepted work, how much is actually right), **false accepts** (the dangerous kind), false rejects.
* Two prompt variants are compared on the same model (`v1-naive`: one-shot, no spec detail, no repair;
  `v2-spec-driven`: structured spec with edge cases, repair loop), and two model sizes.
* **Regression in CI without a model:** every generation is recorded by prompt hash. CI replays the recordings through the
  real workflow and compares the metrics with the committed baseline, so a change to the gate, guardrails, parsing or repair
  logic that alters outcomes fails the build. If a prompt changes, replay raises "no recording" and you re-record. Repair prompts embed pytest output, whose wording differs across Python/pytest versions and platforms (found when CI on Linux/Python 3.10 disagreed with a macOS/3.12 recording), so replay masks that one block and matches on everything else (stage, model, system prompt, spec and previous code).
* Small samples: with 12 tasks the confidence intervals are wide (Wilson 95% intervals are reported). Treat differences
  between variants as directions, not proof.

## What this is not

* Not a production system: no Jira/Linear intake, no real repository checkout or pull request creation, no multi-tenant
  isolation, no auth on the approval signal.
* The local models are small. Results will be much better with a stronger model; the provider interface (`llm.py`) is the
  extension point (an adapter around Claude Code's headless mode or an API model fits there).
* Cost tracking is token counts only; local models cost nothing, so no price table is applied.

## Roadmap

1. A `ClaudeCodeProvider` adapter and a model-routing policy by task difficulty.
2. Real repository checkout, branch + pull request delivery, and CI status as a gate input.
3. Langfuse datasets/experiments so the evaluation shows up as first-class experiments, not only scores.
4. Container sandbox for generated code.
