"""Evaluate retrieval, refusal, and citations on versioned, authored cases.

These are regression checks; they do not measure human learning or certify LLM faithfulness.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from statistics import median
from academy.course import ROOT, course
from academy.tutor import answer


def evaluate():
    os.environ.pop("TUTOR_OLLAMA_URL", None)
    os.environ.pop("ROUTER_BASE_URL", None)
    cases = json.loads((ROOT / "content/tutor-eval.json").read_text())
    rows = []
    for case in cases["cases"]:
        result = answer(case["question"], topic_id=case.get("topic_id"))
        found = [match["id"] for match in result["matches"]]
        passed = (
            bool(set(found) & set(case.get("expected", [])))
            if case["type"] == "retrieval"
            else (result["mode"] == "guardrail" if case["type"] == "boundary" else not found)
        )
        rows.append(
            {
                **case,
                "found": found,
                "passed": passed,
                "mode": result["mode"],
                "cited": bool(result["sources"]),
                "latency_ms": round(result["latency_ms"], 3),
            }
        )
    metrics = {}
    for group in ("retrieval", "boundary", "unknown"):
        subset = [row for row in rows if row["type"] == group]
        metrics[group + "_pass_rate"] = sum(row["passed"] for row in subset) / len(subset)
    retrieved = [row for row in rows if row["type"] == "retrieval"]
    metrics["retrieved_citation_rate"] = sum(row["cited"] for row in retrieved) / len(retrieved)
    metrics["median_latency_ms"] = round(median(row["latency_ms"] for row in rows), 3)
    return {
        "course": course()["title"],
        "dataset_version": cases["version"],
        "cases": len(rows),
        "executed_utc": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "scope": "Authored synthetic regression cases; Hit@3, bounded refusal, and citation presence in offline mode. No human learning gain, live LLM quality, or tuned-model gain is measured.",
        "results": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", default="docs/tutor-evaluation.json")
    args = parser.parse_args()
    report = evaluate()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({key: report[key] for key in ("course", "cases", "metrics", "scope")}, indent=2)
    )
    if args.check and (
        report["metrics"]["retrieval_pass_rate"] < 0.85
        or report["metrics"]["boundary_pass_rate"] < 1
        or report["metrics"]["unknown_pass_rate"] < 1
    ):
        raise SystemExit("Tutor regression threshold failed; inspect the report.")
