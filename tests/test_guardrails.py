"""Policy guardrails and dispatch formatting, exercised without any model call."""

from __future__ import annotations

from vigil.actions.discord import format_dispatch
from vigil.models import Classification, Decision, Incident, IncidentClass, SectorReport
from vigil.swarm.context import RunContext
from vigil.swarm.flow import IncidentFlow, _people_at_risk_in


def _flow(incident: Incident, cls: Classification) -> IncidentFlow:
    ctx = RunContext(incident, None, team="team-test")
    ctx.run.classification = cls
    return IncidentFlow(ctx)


def _decision(action: str) -> Decision:
    return Decision(action=action, confidence=0.7, rationale="r", dispatch_message=f"team-test · x · {action} · r")  # type: ignore[arg-type]


def test_people_parsing():
    assert _people_at_risk_in("4 pinned") == 4
    assert _people_at_risk_in("clear") == 0
    assert _people_at_risk_in("60 present, unaffected") == 0
    assert _people_at_risk_in("1 unresponsive, 15 bystanders") == 1


def test_breached_sector_with_people_is_never_observed():
    inc = Incident(incident_id="i", narrative="n", sector_reports=[SectorReport(sector="bar", civilian_status="4 pinned", containment_state="breached")])
    cls = Classification(incident_class=IncidentClass.environmental_anomaly, severity=4, people_at_risk=4, summary="s")
    d = _flow(inc, cls)._guardrails(_decision("observe"))
    assert d.action == "evacuate" and d.guardrail_note and "evacuate" in d.dispatch_message


def test_control_case_is_never_escalated():
    inc = Incident(incident_id="i", narrative="n", sector_reports=[SectorReport(sector="stage", civilian_status="clear", containment_state="holding")])
    cls = Classification(incident_class=IncidentClass.drill_or_control, severity=1, people_at_risk=0, summary="s", is_control=True)
    d = _flow(inc, cls)._guardrails(_decision("contain"))
    assert d.action == "observe"


def test_infrastructure_fault_with_nobody_at_risk_is_contained():
    inc = Incident(incident_id="i", narrative="n", sector_reports=[SectorReport(sector="closet", civilian_status="clear", containment_state="degraded")])
    cls = Classification(incident_class=IncidentClass.network_outage, severity=2, people_at_risk=0, summary="s")
    d = _flow(inc, cls)._guardrails(_decision("evacuate"))
    assert d.action == "contain"


def test_dispatch_format_starts_with_team_handle():
    msg = format_dispatch("team-test", "lounge", "contain", "smoke   thinning\nafter power cut")
    assert msg.startswith("team-test · lounge · contain · smoke thinning after power cut")
