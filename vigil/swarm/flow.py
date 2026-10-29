"""The incident flow: ingest → classify → (control | specialists → command) → finish.

A CrewAI Flow orchestrates the run. Deterministic branching lives here (@router), autonomy lives
in the specialist crews, and every model output that reaches the UI is schema-locked by Parasail.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar

from crewai.flow import Flow, listen, or_, router, start
from pydantic import BaseModel, Field, PrivateAttr

from ..actions.discord import dispatch
from ..actions.one_client import get_one
from ..catalog import OWNERS, catalog_brief, components_schema, validate_component
from ..config import settings
from ..facility import get_facility
from ..grounding.youcom import get_you
from ..inference.parasail import get_parasail
from ..ingest.feed import ScenarioFeed
from ..models import (
    ActionResult,
    Alternative,
    Classification,
    Component,
    Decision,
    IncidentClass,
    RunbookStep,
    RunTimings,
    SectorReport,
    SignatureMatch,
    SpecialistReport,
)
from .agents import COMMANDER_SYSTEM, SPECIALISTS, build_specialist
from .context import RunContext
from .memory import facility_scope, get_memory, scope_for
from .signatures import get_signatures

INCIDENT_CLASSES = [c.value for c in IncidentClass]

EXTRACT_SYSTEM = (
    "Extract the structure of a facility incident log. Sector ids are snake_case locations named in the log "
    "(for example catering_coffee_bar, main_stage, entrance, server_closet). Readings are numbers stated in the "
    "log with their unit (temperature C, emf mG, co ppm, smoke ppm, occupancy people, power V, network state 0/1). "
    "people_status summarises the people in that sector in the log's own words; state is breached when the "
    "hazard is active there, degraded when partly affected, holding when unaffected, unknown otherwise. Only "
    "include what the log states."
)
EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sectors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sector": {"type": "string"},
                    "readings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"metric": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"}},
                            "required": ["metric", "value", "unit"],
                        },
                    },
                    "people_status": {"type": "string"},
                    "state": {"type": "string", "enum": ["breached", "degraded", "holding", "unknown"]},
                },
                "required": ["sector", "readings", "people_status", "state"],
            },
        },
        "people_at_risk": {"type": "integer"},
    },
    "required": ["sectors", "people_at_risk"],
}

TRIAGE_SYSTEM = (
    "Classify a facility incident for an operations centre. Use one of the incident classes exactly. "
    "is_control is true only for an announced test or drill, nominal activity, or an isolated low-confidence "
    "report with no sensor corroboration. severity runs from 1 (nominal) to 5 (life-threatening now). "
    "people_at_risk counts people trapped, pinned, injured or exposed, not unaffected bystanders. "
    "affected_sectors lists sector ids where the hazard is present. summary: two plain sentences, no speculation."
)
TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "incident_class": {"type": "string", "enum": INCIDENT_CLASSES},
        "severity": {"type": "integer", "minimum": 1, "maximum": 5},
        "people_at_risk": {"type": "integer"},
        "affected_sectors": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "is_control": {"type": "boolean"},
        "title": {"type": "string"},
    },
    "required": ["incident_class", "severity", "people_at_risk", "affected_sectors", "summary", "is_control", "title"],
}

MONITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rationale": {"type": "string"},
        "message": {"type": "string"},
        "next_check": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rationale", "message", "next_check", "steps"],
}

_PEOPLE_RE = re.compile(r"(\d+)\s*(?:people|persons|staff|engineers|attendees|present|pinned|trapped|injured|unresponsive)", re.I)


def _people_in(status: str) -> int:
    if not status:
        return 0
    if re.search(r"\b(clear|none|unaffected only)\b", status, re.I) and not re.search(r"\d", status):
        return 0
    m = _PEOPLE_RE.search(status)
    if m:
        return int(m.group(1))
    if re.search(r"pinned|trapped|injured|unresponsive", status, re.I):
        return 1
    return 0


def _people_at_risk_in(status: str) -> int:
    """People who are actually in trouble in a sector, not merely present."""
    if not status or not re.search(r"pinned|trapped|injured|unresponsive|coughing|exposed|collapsed", status, re.I):
        return 0
    return max(1, _people_in(status))


class FlowState(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str = ""


class IncidentFlow(Flow[FlowState]):
    initial_state = FlowState
    suppress_flow_events: ClassVar[bool] = True  # keep CrewAI's console boxes out of the event stream
    _ctx: Any = PrivateAttr(default=None)

    def __init__(self, ctx: RunContext):
        super().__init__()
        self._ctx = ctx

    @property
    def ctx(self) -> RunContext:
        return self._ctx

    # ------------------------------------------------------------------ 1. ingest
    @start()
    async def ingest(self) -> str:
        ctx = self.ctx
        inc = ctx.run.incident
        self.state.run_id = ctx.run.run_id
        ctx.run.timings = RunTimings()  # the clock starts when the swarm receives the incident
        ctx.set_status("classifying")
        ctx.emit(
            "run.created",
            incident={
                "incident_id": inc.incident_id,
                "source": inc.source,
                "facility_id": inc.facility_id,
                "tick_id": inc.tick_id,
                "scenario": inc.scenario,
                "narrative": inc.narrative,
                "sector_reports": [r.model_dump() for r in inc.sector_reports],
                "expected_action": inc.expected_action,
            },
            facility=ctx.facility.layout(),
            team=ctx.team,
            auto_act=ctx.auto_act,
        )
        if not inc.sector_reports:
            ctx.log("No structured readings in the log; extracting structure from the text.")
            data = await get_parasail().structured(
                name="extract_structure", schema=EXTRACT_SCHEMA, system=EXTRACT_SYSTEM, user=inc.narrative, max_tokens=1200
            )
            ctx.run.llm_calls += 1
            for s in data.get("sectors", []):
                sensors = {}
                for r in s.get("readings", []):
                    metric = re.sub(r"[^a-z0-9]+", "_", str(r.get("metric", "")).lower()).strip("_")
                    if metric:
                        sensors[metric] = float(r.get("value", 0))
                inc.sector_reports.append(
                    SectorReport(
                        sector=re.sub(r"[^a-z0-9]+", "_", str(s.get("sector", "area")).lower()).strip("_") or "area",
                        sensors=sensors,
                        civilian_status=s.get("people_status", "unknown"),
                        containment_state=s.get("state", "unknown"),
                    )
                )
            ctx.facility = get_facility(inc.facility_id, inc.sectors())
            ctx.emit("facility", facility=ctx.facility.layout())
        ctx.add_component(
            Component(
                component="IncidentHeader",
                owner="system",
                order=1,
                key="system:IncidentHeader",
                props={
                    "incident_id": inc.incident_id,
                    "title": "Classifying incident…",
                    "facility": ctx.facility.name,
                    "incident_class": "pending",
                    "severity": 0,
                    "source": inc.source,
                    "received_at": inc.received_at.isoformat(timespec="seconds"),
                },
            )
        )
        return "ingested"

    # ------------------------------------------------------------------ 2. classify
    @listen(ingest)
    async def classify(self) -> str:
        ctx = self.ctx
        inc = ctx.run.incident
        text = inc.narrative + "\n" + ctx.sensor_view()
        matches = await asyncio.to_thread(get_signatures().match, text, 3)
        ctx.emit("signature", matches=matches)
        sig_lines = "\n".join(f"- {m['label']} (similarity {m['score']:.2f}) → class {m['incident_class']}, usual response {m['typical_action']}" for m in matches)
        user = (
            f"INCIDENT LOG:\n{inc.narrative}\n\nSTRUCTURED READINGS:\n{ctx.sensor_view()}\n\n"
            f"SECTOR STATUS:\n" + "\n".join(f"- {r.sector}: people '{r.civilian_status}', state '{r.containment_state}'" for r in inc.sector_reports)
            + f"\n\nCLOSEST HAZARD SIGNATURES (advisory):\n{sig_lines}"
        )
        data = await get_parasail().structured(
            name="triage", schema=TRIAGE_SCHEMA, system=TRIAGE_SYSTEM, user=user, max_tokens=900
        )
        ctx.run.llm_calls += 1
        try:
            cls_value = IncidentClass(data["incident_class"])
        except ValueError:
            cls_value = IncidentClass.unknown
        people_from_status = sum(_people_at_risk_in(r.civilian_status) for r in inc.sector_reports)
        classification = Classification(
            incident_class=cls_value,
            severity=int(max(1, min(5, data.get("severity", 3)))),
            people_at_risk=max(int(data.get("people_at_risk", 0)), people_from_status),
            affected_sectors=[s for s in data.get("affected_sectors", []) if isinstance(s, str)],
            summary=data.get("summary", ""),
            is_control=bool(data.get("is_control")) or cls_value == IncidentClass.drill_or_control,
            signature=SignatureMatch(label=matches[0]["label"], score=matches[0]["score"], description=matches[0]["indicators"]) if matches else None,
            latency_ms=ctx.run.timings.mark(),
        )
        # A breached sector with people in trouble is never a control case, whatever the model said.
        if any(r.containment_state == "breached" and _people_at_risk_in(r.civilian_status) > 0 for r in inc.sector_reports):
            classification.is_control = False
        ctx.run.classification = classification
        ctx.run.timings.classified_ms = ctx.run.timings.mark()
        title = data.get("title") or classification.summary.split(".")[0]
        ctx.add_component(
            Component(
                component="IncidentHeader",
                owner="system",
                order=1,
                key="system:IncidentHeader",
                props={
                    "incident_id": inc.incident_id,
                    "title": title[:90],
                    "facility": ctx.facility.name,
                    "incident_class": classification.incident_class.value,
                    "severity": classification.severity,
                    "source": inc.source,
                    "received_at": inc.received_at.isoformat(timespec="seconds"),
                },
            )
        )
        labels = {1: "Nominal", 2: "Minor", 3: "Serious", 4: "Severe", 5: "Life-threatening"}
        ctx.add_component(
            Component(
                component="SeverityBanner",
                owner="system",
                order=2,
                key="system:SeverityBanner",
                props={
                    "level": classification.severity,
                    "label": "Control / no incident" if classification.is_control else labels[classification.severity],
                    "people_at_risk": classification.people_at_risk,
                    "is_control": classification.is_control,
                },
            )
        )
        ctx.emit("classification", classification=classification.model_dump(mode="json"))
        return "classified"

    @router(classify)
    def route(self) -> str:
        cls = self.ctx.run.classification
        return "control" if cls and cls.is_control else "respond"

    # ------------------------------------------------------------------ 3a. control path
    @listen("control")
    async def monitor(self) -> str:
        ctx = self.ctx
        cls = ctx.run.classification
        assert cls is not None
        ctx.set_status("deciding")
        ctx.log("Control case: the station will monitor and will not escalate.")
        data = await get_parasail().structured(
            name="monitor_plan",
            schema=MONITOR_SCHEMA,
            system=(
                "You are the incident commander. The case has been classified as a control or test with no hazard. "
                "Write a one-sentence rationale for observing, a one-line status message starting with the team handle in the form "
                "'<team> · <sector> · observe · <rationale>', when to check again, and 2 to 4 monitoring steps."
            ),
            user=f"TEAM HANDLE: {ctx.team}\n{ctx.narrative_view()}\n\n{ctx.sensor_view()}",
            max_tokens=600,
        )
        ctx.run.llm_calls += 1
        message = data.get("message", "").strip()
        data["rationale"] = re.sub(r"^[\s,;:.-]+", "", data.get("rationale", ""))
        if not message.startswith(ctx.team):
            sector = cls.affected_sectors[0] if cls.affected_sectors else (ctx.run.incident.sectors() or ["facility"])[0]
            message = f"{ctx.team} · {sector} · observe · {data.get('rationale', 'nominal readings')}"
        ctx.add_component(Component(component="MonitorPanel", owner="commander", order=10, key="commander:MonitorPanel", props={"message": data.get("rationale", ""), "next_check": data.get("next_check", "next tick")}))
        ctx.add_component(Component(component="DecisionPanel", owner="commander", order=11, key="commander:DecisionPanel", props={"action": "observe", "confidence": 0.9, "rationale": data.get("rationale", ""), "alternatives": [{"action": "contain", "why_not": "No hazard is present to contain."}, {"action": "evacuate", "why_not": "Moving people during a test creates risk without benefit."}]}))
        ctx.add_component(Component(component="DispatchComposer", owner="commander", order=12, key="commander:DispatchComposer", props={"channel": "#loft-incident", "message": message, "requires_approval": True}))
        steps = [RunbookStep(id=f"m{i + 1}", text=s, owner="incident_commander") for i, s in enumerate(data.get("steps", []))]
        ctx.add_component(Component(component="Runbook", owner="commander", order=13, key="commander:Runbook", props={"steps": [s.model_dump() for s in steps]}))
        ctx.run.decision = Decision(
            action="observe", confidence=0.9, rationale=data.get("rationale", ""), dispatch_message=message, runbook=steps,
            alternatives=[Alternative(action="contain", why_not="No hazard present."), Alternative(action="evacuate", why_not="No benefit during a test.")],
            latency_ms=ctx.run.timings.mark(),
        )
        ctx.run.timings.decided_ms = ctx.run.timings.mark()
        ctx.emit("decision", decision=ctx.run.decision.model_dump(mode="json"))
        ctx.set_status("awaiting_operator")
        return "monitoring"

    # ------------------------------------------------------------------ 3b. specialists
    @listen("respond")
    async def specialists(self) -> str:
        ctx = self.ctx
        ctx.set_status("specialists")
        reports = await asyncio.gather(*(self._run_specialist(k) for k in SPECIALISTS))
        ctx.run.reports = list(reports)
        ctx.run.timings.specialists_ms = ctx.run.timings.mark()
        ctx.emit_sources()
        return "reported"

    async def _run_specialist(self, key: str) -> SpecialistReport:
        ctx = self.ctx
        spec = SPECIALISTS[key]
        t0 = time.time()
        tool_calls_before = sum(1 for e in ctx.events if e.get("type") == "agent.tool" and e.get("agent") == key)
        ctx.emit("agent.status", agent=key, role=spec.role, state="investigating")
        # Provisional pass: stream components from the raw view right away, so the operator has a
        # first interface in seconds. The verified pass below replaces them in place.
        provisional = asyncio.create_task(
            self._compile(key, spec.compile_system, "Provisional pass, no investigation yet. " + spec.view(ctx), provisional=True)
        )
        error: str | None = None
        try:
            _agent, crew = await asyncio.to_thread(build_specialist, ctx, spec)
            result = await asyncio.wait_for(crew.kickoff_async(), timeout=180)
            findings = str(getattr(result, "raw", result)).strip()
            if not findings:
                raise RuntimeError("empty findings")
        except Exception as e:  # keep the station alive: compile from the raw view instead
            error = f"{type(e).__name__}: {str(e)[:200]}"
            ctx.log(f"{spec.role}: investigation failed ({error}); compiling from raw readings.", "warn")
            findings = spec.view(ctx)
        try:
            await provisional
        except Exception as e:
            ctx.log(f"{spec.role}: provisional compile failed ({str(e)[:120]})", "warn")
        ctx.emit("agent.status", agent=key, role=spec.role, state="compiling", findings=findings[:2000])
        components = await self._compile(key, spec.compile_system, findings)
        latency = int((time.time() - t0) * 1000)
        tool_calls = sum(1 for e in ctx.events if e.get("type") == "agent.tool" and e.get("agent") == key) - tool_calls_before
        ctx.emit("agent.status", agent=key, role=spec.role, state="done", components=[c.component for c in components], latency_ms=latency, tool_calls=tool_calls)
        asyncio.get_running_loop().run_in_executor(None, self._remember, key, findings)
        return SpecialistReport(
            agent=key, findings=findings, components=components,
            sources=[s for s in ctx.run.sources], tool_calls=tool_calls, latency_ms=latency, error=error,
        )

    def _remember(self, key: str, findings: str) -> None:
        try:
            mem = get_memory()
            mem.remember(findings[:3000], scope=scope_for(self.ctx.run.run_id, key), categories=["incident", key], importance=0.7, source=key)
        except Exception as e:  # memory is an accelerator, never a blocker
            self.ctx.log(f"memory write skipped for {key}: {str(e)[:120]}", "warn")

    async def _compile(self, owner: str, system: str, findings: str, *, model: str | None = None, extra_user: str = "", provisional: bool = False) -> list[Component]:
        ctx = self.ctx
        names = OWNERS[owner]
        schema = components_schema(names)
        context_block = ""
        if owner == "sensors":
            context_block = f"\n\nREADINGS:\n{ctx.sensor_view()}"
        elif owner == "facility":
            context_block = f"\n\nSECTORS:\n{ctx.facility_view()}"
        elif owner == "research":
            context_block = f"\n\nINCIDENT:\n{ctx.narrative_view()[:1500]}"
        user = (
            f"FINDINGS:\n{findings}{context_block}\n\nSOURCES (the only ids you may cite):\n{ctx.sources_text()}\n\n"
            f"YOUR COMPONENTS:\n{catalog_brief(names)}{extra_user}"
        )
        comps: list[Component] = []
        order_base = {"sensors": 100, "facility": 200, "research": 300, "commander": 400}[owner]
        async for ev in get_parasail().stream_structured(
            name=f"{owner}_components", schema=schema, system=system, user=user,
            model=model or settings.model_fast, max_tokens=3000,
        ):
            if ev["type"] == "item":
                try:
                    comp = validate_component(ev["item"], owner, order_base + len(comps), provisional=provisional)
                    comp.props = ctx.scrub_source_ids(comp.props)
                    ctx.add_component(comp)
                    comps.append(comp)
                except Exception as e:
                    ctx.invalid_components += 1
                    ctx.emit("component.invalid", owner=owner, error=str(e)[:200])
            elif ev["type"] == "final":
                ctx.run.llm_calls += 1
                ctx.emit("compile.done", owner=owner, latency_ms=ev.get("latency_ms"), replayed=ev.get("replayed", False), count=len(comps), provisional=provisional)
        return comps

    # ------------------------------------------------------------------ 4. command
    @listen(specialists)
    async def command(self) -> str:
        ctx = self.ctx
        ctx.set_status("deciding")
        cls = ctx.run.classification
        assert cls is not None
        recall_text = await asyncio.to_thread(self._recall, cls.summary)
        findings = "\n\n".join(
            f"## {SPECIALISTS[r.agent].role}{' (investigation failed; raw readings)' if r.error else ''}\n{r.findings[:3500]}" for r in ctx.run.reports
        )
        user = (
            f"TEAM HANDLE: {ctx.team}\n\n{ctx.narrative_view()}\n\nSECTOR STATUS:\n{ctx.facility_view()}\n\n"
            f"SPECIALIST FINDINGS:\n{findings}\n\nMEMORY (earlier findings and prior incidents at this facility):\n{recall_text}\n\n"
            f"SOURCES (the only ids you may cite):\n{ctx.sources_text()}\n\nYOUR COMPONENTS:\n{catalog_brief(OWNERS['commander'])}"
        )
        comps: list[Component] = []
        async for ev in get_parasail().stream_structured(
            name="command", schema=components_schema(OWNERS["commander"]), system=COMMANDER_SYSTEM, user=user,
            model=settings.model_commander, max_tokens=2500, deliberate=settings.deliberate,
        ):
            if ev["type"] == "item":
                try:
                    comp = validate_component(ev["item"], "commander", 400 + len(comps))
                    comp.props = ctx.scrub_source_ids(comp.props)
                    comps.append(comp)
                    ctx.add_component(comp)
                except Exception as e:
                    ctx.invalid_components += 1
                    ctx.emit("component.invalid", owner="commander", error=str(e)[:200])
            elif ev["type"] == "final":
                ctx.run.llm_calls += 1
        by_name = {c.component: c for c in comps}
        if "DecisionPanel" not in by_name:
            ctx.log("Commander did not emit a decision; requesting one directly.", "warn")
            data = await get_parasail().structured(
                name="decision", schema=components_schema(["DecisionPanel"]), system=COMMANDER_SYSTEM, user=user,
                model=settings.model_commander, max_tokens=900,
            )
            ctx.run.llm_calls += 1
            for item in data.get("components", []):
                comp = validate_component(item, "commander", 400)
                by_name["DecisionPanel"] = comp
                ctx.add_component(comp)
        dp = by_name["DecisionPanel"].props
        dc = by_name.get("DispatchComposer")
        rb = by_name.get("Runbook")
        sector = cls.affected_sectors[0] if cls.affected_sectors else (ctx.run.incident.sectors() or ["facility"])[0]
        message = dc.props["message"] if dc else f"{ctx.team} · {sector} · {dp['action']} · {dp['rationale'][:140]}"
        decision = Decision(
            action=dp["action"],
            confidence=float(max(0.0, min(1.0, dp.get("confidence", 0.5)))),
            rationale=dp.get("rationale", ""),
            dispatch_message=message,
            runbook=[RunbookStep(**s) for s in (rb.props["steps"] if rb else [])],
            alternatives=[Alternative(**a) for a in dp.get("alternatives", [])],
            latency_ms=ctx.run.timings.mark(),
        )
        decision = self._guardrails(decision)
        if decision.guardrail_note:
            dp["action"] = decision.action
            dp["rationale"] = decision.rationale
            ctx.add_component(by_name["DecisionPanel"])
            if dc:
                dc.props["message"] = decision.dispatch_message
                ctx.add_component(dc)
        if not decision.dispatch_message.startswith(ctx.team):
            decision.dispatch_message = f"{ctx.team} · {sector} · {decision.action} · {decision.rationale[:140]}"
            if dc:
                dc.props["message"] = decision.dispatch_message
                ctx.add_component(dc)
        ctx.run.decision = decision
        ctx.run.timings.decided_ms = ctx.run.timings.mark()
        ctx.emit("decision", decision=decision.model_dump(mode="json"))
        ctx.emit_sources()
        ctx.set_status("acting" if ctx.auto_act else "awaiting_operator")
        return "decided"

    def _recall(self, query: str) -> str:
        try:
            mem = get_memory()
            scopes = [scope_for(self.ctx.run.run_id, k) for k in SPECIALISTS] + [facility_scope(self.ctx.run.incident.facility_id)]
            view = mem.slice(scopes=scopes, read_only=True)
            hits = view.recall(query[:300] or "incident", limit=6, depth="shallow")
            lines = [f"- [{h.record.scope}] {h.record.content[:300]}" for h in hits]
            return "\n".join(lines) or "(nothing relevant in memory)"
        except Exception as e:
            return f"(memory unavailable: {str(e)[:100]})"

    def _guardrails(self, decision: Decision) -> Decision:
        ctx = self.ctx
        inc = ctx.run.incident
        cls = ctx.run.classification
        assert cls is not None
        notes: list[str] = []
        breached_with_people = any(
            r.containment_state == "breached" and _people_at_risk_in(r.civilian_status) > 0 for r in inc.sector_reports
        )
        if cls.is_control and decision.action != "observe":
            notes.append("policy: control cases are observed, never escalated")
            decision.action = "observe"
        if breached_with_people and decision.action == "observe":
            notes.append("policy: people in a breached sector are never left under observation")
            decision.action = "evacuate"
        if (
            decision.action == "evacuate"
            and cls.people_at_risk == 0
            and not breached_with_people
            and cls.incident_class in {IncidentClass.network_outage, IncidentClass.power_failure}
            and cls.severity <= 3
        ):
            notes.append("policy: infrastructure faults with nobody at risk are contained, not evacuated")
            decision.action = "contain"
        if notes:
            decision.guardrail_note = "; ".join(notes)
            decision.rationale = f"{decision.rationale} Adjusted by policy: {decision.guardrail_note}."
            parts = decision.dispatch_message.split(" · ")
            if len(parts) >= 3:
                parts[2] = decision.action
                decision.dispatch_message = " · ".join(parts)
            ctx.log(f"Guardrail applied: {decision.guardrail_note}", "warn")
        return decision

    # ------------------------------------------------------------------ 5. finish
    @listen(or_(monitor, command))
    async def finish(self) -> str:
        ctx = self.ctx
        if ctx.auto_act and ctx.run.decision is not None:
            await perform_actions(ctx)
        ctx.run.timings.total_ms = ctx.run.timings.mark()
        if ctx.run.status not in ("awaiting_operator", "failed", "done"):
            ctx.set_status("done")
        ctx.emit(
            "metrics",
            timings=ctx.run.timings.model_dump(),
            llm=get_parasail().ledger.summary(),
            grounding=get_you().summary(),
            llm_calls=ctx.run.llm_calls,
            sources=len(ctx.run.sources),
            components=len(ctx.run.components),
            invalid_components=ctx.invalid_components,
        )
        return "finished"


# ---------------------------------------------------------------------- actions
def _after_action_body(ctx: RunContext) -> str:
    run = ctx.run
    inc = run.incident
    cls = run.classification
    d = run.decision
    assert cls is not None and d is not None
    lines = [
        f"# Post-incident review: {inc.incident_id}",
        "",
        f"- Facility: {ctx.facility.name}, {ctx.facility.address}",
        f"- Received: {inc.received_at.isoformat(timespec='seconds')} (source: {inc.source}, tick {inc.tick_id})",
        f"- Classification: **{cls.incident_class.value}**, severity {cls.severity}/5, {cls.people_at_risk} people at risk",
        f"- Decision: **{d.action}** (confidence {d.confidence:.2f})",
        f"- Rationale: {d.rationale}",
        f"- Dispatch: `{d.dispatch_message}`",
        "",
        "## Incident log",
        "",
        "```",
        inc.narrative[:2000],
        "```",
        "",
        "## Runbook",
        "",
    ]
    for s in d.runbook:
        cite = f" [{s.source_id}]" if s.source_id else ""
        lines.append(f"- [ ] {s.text} — _{s.owner}_{cite}")
    lines += ["", "## Specialist findings", ""]
    for r in run.reports:
        lines += [f"### {SPECIALISTS[r.agent].role}", "", r.findings[:2500], ""]
    lines += ["## Sources", ""]
    for s in run.sources:
        lines.append(f"- [{s.id}] [{s.title}]({s.url})")
    t = run.timings
    lines += [
        "",
        "## Run metrics",
        "",
        f"- First component: {t.first_component_ms} ms; classified: {t.classified_ms} ms; specialists: {t.specialists_ms} ms; decided: {t.decided_ms} ms",
        f"- Model calls: {run.llm_calls}; grounding sources: {len(run.sources)}; components: {len(run.components)}",
        "",
        "_Opened automatically by Vigil through One's GitHub connection._",
    ]
    return "\n".join(lines)


async def perform_actions(ctx: RunContext, *, override_action: str | None = None, dry_run: bool | None = None) -> list[ActionResult]:
    """Dispatch, report to the feed, and open the after-action issue. Called by the flow in auto mode or by the operator."""
    run = ctx.run
    d = run.decision
    if d is None:
        return []
    if override_action and override_action != d.action:
        d.rationale = f"Operator override: {override_action} (swarm proposed {d.action}). {d.rationale}"
        d.action = override_action  # type: ignore[assignment]
        parts = d.dispatch_message.split(" · ")
        if len(parts) >= 3:
            parts[2] = d.action
            d.dispatch_message = " · ".join(parts)
        ctx.emit("decision", decision=d.model_dump(mode="json"))
    ctx.set_status("acting")
    dry = settings.dry_run if dry_run is None else dry_run
    results: list[ActionResult] = []

    if d.action != "observe":
        res = await asyncio.to_thread(dispatch, d.dispatch_message, dry_run=dry, team=ctx.team)
        results.append(res)
        ctx.emit("action", result=res.model_dump())

    inc = run.incident
    if inc.source == "feed" and inc.tick_id is not None:
        try:
            feed = ScenarioFeed(team=ctx.team)
            judgment = await feed.asubmit_containment(inc.tick_id, d.action)
            res = ActionResult(kind="feed_submit", ok=True, detail={"tick_id": inc.tick_id, "action": d.action, **(judgment if isinstance(judgment, dict) else {"raw": str(judgment)})})
        except Exception as e:
            res = ActionResult(kind="feed_submit", ok=False, detail={"tick_id": inc.tick_id, "action": d.action, "error": str(e)[:300]})
        results.append(res)
        ctx.emit("action", result=res.model_dump())

    title = f"Post-incident review: {inc.incident_id} — {d.action} ({run.classification.incident_class.value if run.classification else 'unknown'})"
    res = await asyncio.to_thread(get_one().create_github_issue, title=title, body=_after_action_body(ctx), labels=["post-incident", d.action], dry_run=dry)
    results.append(res)
    ctx.emit("action", result=res.model_dump())

    def _remember_outcome() -> None:
        try:
            get_memory().remember(
                f"{datetime.now(timezone.utc).date()}: {run.classification.incident_class.value if run.classification else 'incident'} at "
                f"{ctx.facility.name} (sectors {', '.join(inc.sectors())}). Decision {d.action}: {d.rationale[:300]}",
                scope=facility_scope(inc.facility_id), categories=["outcome"], importance=0.8, source="commander",
            )
        except Exception:
            pass

    asyncio.get_running_loop().run_in_executor(None, _remember_outcome)
    run.actions.extend(results)
    ctx.set_status("done")
    return results


_warmed = False


async def prewarm() -> None:
    """Embed the signature corpus and open memory once, so the first run is not slowed by setup."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    try:
        await asyncio.gather(
            asyncio.to_thread(get_signatures()._ensure),
            asyncio.to_thread(get_memory),
        )
    except Exception:
        pass


async def run_context(ctx: RunContext) -> RunContext:
    """Run the flow on a prepared context (the server creates the context first so the run id is known)."""
    await prewarm()
    flow = IncidentFlow(ctx)
    try:
        await flow.kickoff_async()
    except Exception as e:
        ctx.run.error = f"{type(e).__name__}: {str(e)[:300]}"
        ctx.set_status("failed")
        ctx.log(f"Run failed: {ctx.run.error}", "error")
    return ctx


async def run_incident(incident, *, emit=None, team: str | None = None, auto_act: bool = False) -> RunContext:  # type: ignore[no-untyped-def]
    await prewarm()
    ctx = RunContext(incident, emit, team=team or settings.team_handle, auto_act=auto_act)
    return await run_context(ctx)
