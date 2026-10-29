"""Evaluation harness.

Runs every fixture tick through the swarm (no actions) and reports what a judge or an on-call
lead would ask about: did it decide correctly, did it escalate on a control case, how fast did
the first component and the decision appear, how many components passed schema validation, and
how many recommendations carry a real citation.

    python -m vigil eval            # all fixtures
    python -m vigil eval --json     # machine-readable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Any

from ..ingest.fixtures import list_fixtures, load_fixture
from ..inference.parasail import get_parasail
from ..grounding.youcom import get_you
from ..swarm.flow import run_incident


def _citation_coverage(ctx) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    with_cite = total = 0
    for c in ctx.run.components:
        if c.provisional:
            continue
        if c.component == "ResponseOptions":
            for o in c.props.get("options", []):
                total += 1
                with_cite += 1 if o.get("source_ids") else 0
        if c.component == "Runbook":
            for s in c.props.get("steps", []):
                total += 1
                with_cite += 1 if s.get("source_id") else 0
    return with_cite, total


async def evaluate(names: list[str] | None = None, quiet: bool = True) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    fixtures = [f["name"] for f in list_fixtures()]
    if names:
        fixtures = [f for f in fixtures if f in names]
    for name in fixtures:
        for inc in load_fixture(name):
            t0 = time.time()
            ctx = await run_incident(inc, emit=None if quiet else print, auto_act=False)
            run = ctx.run
            decided = run.decision.action if run.decision else None
            with_cite, total = _citation_coverage(ctx)
            final_components = [c for c in run.components if not c.provisional]
            rows.append(
                {
                    "fixture": name,
                    "tick": inc.tick_id,
                    "expected": inc.expected_action,
                    "decided": decided,
                    "correct": (decided == inc.expected_action) if inc.expected_action else None,
                    "is_control": bool(run.classification and run.classification.is_control),
                    "false_alarm": bool(inc.expected_action == "observe" and decided != "observe"),
                    "incident_class": run.classification.incident_class.value if run.classification else None,
                    "first_component_ms": run.timings.first_component_ms,
                    "decided_ms": run.timings.decided_ms,
                    "wall_ms": int((time.time() - t0) * 1000),
                    "components": len(final_components),
                    "invalid_components": ctx.invalid_components,
                    "sources": len(run.sources),
                    "cited": with_cite,
                    "citable": total,
                    "tool_calls": sum(r.tool_calls for r in run.reports),
                    "specialist_errors": sum(1 for r in run.reports if r.error),
                    "guardrail": run.decision.guardrail_note if run.decision else None,
                    "error": run.error,
                }
            )
            if not quiet:
                print(json.dumps(rows[-1]))
    scored = [r for r in rows if r["correct"] is not None]
    controls = [r for r in rows if r["expected"] == "observe"]
    summary = {
        "ticks": len(rows),
        "decision_accuracy": round(sum(1 for r in scored if r["correct"]) / len(scored), 3) if scored else None,
        "false_alarm_rate": round(sum(1 for r in controls if r["false_alarm"]) / len(controls), 3) if controls else None,
        "schema_validity": round(
            1 - sum(r["invalid_components"] for r in rows) / max(1, sum(r["components"] + r["invalid_components"] for r in rows)), 3
        ),
        "citation_coverage": round(sum(r["cited"] for r in rows) / max(1, sum(r["citable"] for r in rows)), 3),
        "median_first_component_ms": statistics.median([r["first_component_ms"] for r in rows if r["first_component_ms"] is not None]) if rows else None,
        "median_decided_ms": statistics.median([r["decided_ms"] for r in rows if r["decided_ms"] is not None]) if rows else None,
        "sources_total": sum(r["sources"] for r in rows),
        "specialist_errors": sum(r["specialist_errors"] for r in rows),
        "llm": get_parasail().ledger.summary(),
        "grounding": get_you().summary(),
    }
    return {"rows": rows, "summary": summary}


def render_table(result: dict[str, Any]) -> str:
    rows = result["rows"]
    head = f"{'fixture':34} {'tick':>4} {'expected':>9} {'decided':>9} {'ok':>3} {'class':22} {'1st ms':>7} {'dec ms':>7} {'comp':>4} {'src':>4} {'cite':>7} {'tools':>5}"
    lines = [head, "-" * len(head)]
    for r in rows:
        ok = "yes" if r["correct"] else ("no" if r["correct"] is False else "-")
        lines.append(
            f"{r['fixture']:34} {str(r['tick']):>4} {str(r['expected']):>9} {str(r['decided']):>9} {ok:>3} {str(r['incident_class']):22} "
            f"{str(r['first_component_ms']):>7} {str(r['decided_ms']):>7} {r['components']:>4} {r['sources']:>4} {r['cited']:>3}/{r['citable']:<3} {r['tool_calls']:>5}"
        )
    s = result["summary"]
    lines += [
        "",
        f"decision accuracy      {s['decision_accuracy']}",
        f"false-alarm rate       {s['false_alarm_rate']}",
        f"schema validity        {s['schema_validity']}",
        f"citation coverage      {s['citation_coverage']}",
        f"median first component {s['median_first_component_ms']} ms",
        f"median decision        {s['median_decided_ms']} ms",
        f"sources gathered       {s['sources_total']}",
        f"model calls            {s['llm']['calls']} (cached {s['llm']['cached']}, p50 {s['llm']['p50_latency_ms']} ms)",
        f"grounding calls        {s['grounding']['calls']} (cached {s['grounding']['cached']})",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vigil eval")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only", nargs="*", help="fixture names to include")
    parser.add_argument("--out", help="write the JSON report here")
    args = parser.parse_args(argv if argv is not None else sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "eval" else sys.argv[1:])
    result = asyncio.run(evaluate(args.only))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str) if args.json else render_table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
