"""Compose the post-incident review that One files as a GitHub issue.

Kept compact on purpose: the One CLI takes the request body on the command line, and Windows
caps a command line at roughly 32k characters. Findings and sources are trimmed accordingly.
"""

from __future__ import annotations

from ..swarm.context import RunContext

MAX_BODY = 9000


def after_action_title(ctx: RunContext) -> str:
    run = ctx.run
    cls = run.classification.incident_class.value if run.classification else "unknown"
    action = run.decision.action if run.decision else "undecided"
    return f"Post-incident review: {run.incident.incident_id} - {action} ({cls})"


def after_action_body(ctx: RunContext) -> str:
    from ..swarm.agents import SPECIALISTS

    run = ctx.run
    inc = run.incident
    cls = run.classification
    d = run.decision
    assert cls is not None and d is not None
    lines = [
        f"## Summary",
        "",
        f"- Facility: {ctx.facility.name}, {ctx.facility.address}",
        f"- Received: {inc.received_at.isoformat(timespec='seconds')} (source: {inc.source}, tick {inc.tick_id})",
        f"- Classification: **{cls.incident_class.value}**, severity {cls.severity}/5, {cls.people_at_risk} people at risk",
        f"- Decision: **{d.action}** (confidence {d.confidence:.2f})",
        f"- Rationale: {d.rationale[:600]}",
        f"- Dispatch: `{d.dispatch_message[:400]}`",
    ]
    if d.guardrail_note:
        lines.append(f"- Policy adjustment: {d.guardrail_note}")
    lines += ["", "## Incident log", "", "```", inc.narrative[:1200], "```", "", "## Runbook", ""]
    for s in d.runbook[:8]:
        cite = f" [{s.source_id}]" if s.source_id else ""
        lines.append(f"- [ ] {s.text[:200]} ({s.owner}){cite}")
    lines += ["", "## Specialist findings", ""]
    for r in run.reports:
        lines += [f"### {SPECIALISTS[r.agent].role}" + (" (investigation failed, raw readings used)" if r.error else ""), "", r.findings[:700].strip(), ""]
    lines += ["## Sources", ""]
    for s in run.sources[:12]:
        lines.append(f"- [{s.id}] [{s.title[:80]}]({s.url})")
    if len(run.sources) > 12:
        lines.append(f"- ... and {len(run.sources) - 12} more in the run record")
    t = run.timings
    lines += [
        "",
        "## Run metrics",
        "",
        f"- First component {t.first_component_ms} ms, classified {t.classified_ms} ms, specialists {t.specialists_ms} ms, decided {t.decided_ms} ms",
        f"- Model calls {run.llm_calls}, grounding sources {len(run.sources)}, components {len(run.components)}",
        "",
        "_Filed automatically by Vigil through One's GitHub connection._",
    ]
    body = "\n".join(lines)
    if len(body) > MAX_BODY:
        body = body[: MAX_BODY - 40] + "\n\n_(truncated for the command line)_"
    return body
