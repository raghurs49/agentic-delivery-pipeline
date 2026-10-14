#!/usr/bin/env python3
"""Render evals/results/*.json as Markdown tables; with --update-readme, rewrite the block between
<!-- results:start --> and <!-- results:end --> in README.md so documented numbers always come from the data."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def render() -> str:
    out = []
    files = sorted((ROOT / "evals" / "results").glob("*.json"))
    if not files:
        return "_No evaluation results committed yet._\n"
    out.append("| Model | Variant | Hidden tests pass | 95% CI | Gate accepted | Gate precision | False accepts | False rejects | Avg repairs | LLM time / task |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    notes = []
    for f in files:
        d = json.loads(f.read_text())
        model = d["meta"]["code_model"]
        for name, v in d["variants"].items():
            m = v["metrics"]
            ci = m["hidden_pass_ci95"]
            out.append(f"| {model} | {name} | {m['hidden_pass']}/{m['n_tasks']} ({pct(m['hidden_pass_rate'])}) | {pct(ci[0])}-{pct(ci[1])} | "
                       f"{m['gate_accepted']}/{m['n_tasks']} | {pct(m['gate_precision'])} | {m['false_accepts']} | {m['false_rejects']} | "
                       f"{m['avg_repair_iterations']} | {m['avg_llm_latency_s']}s |")
        notes.append((model, d["variants"]))
    out.append("")
    out.append("Outcome mix per run (status counts):")
    out.append("")
    for model, variants in notes:
        for name, v in variants.items():
            mix = ", ".join(f"{k} {n}" for k, n in sorted(v["metrics"]["statuses"].items()))
            out.append(f"- `{model}` / `{name}`: {mix}")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-readme", action="store_true")
    a = ap.parse_args()
    table = render()
    if not a.update_readme:
        print(table)
        return
    p = ROOT / "README.md"
    s = p.read_text()
    new = re.sub(r"(<!-- results:start -->\n).*?(<!-- results:end -->)", lambda m: m.group(1) + "\n" + table + "\n" + m.group(2), s, flags=re.S)
    if new == s and "<!-- results:start -->" not in s:
        raise SystemExit("README.md has no results markers")
    p.write_text(new)
    print("README.md results updated")


if __name__ == "__main__":
    main()
