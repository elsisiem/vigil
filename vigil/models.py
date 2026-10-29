"""Core data model shared by ingestion, the swarm, the UI stream and the eval harness."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IncidentClass(str, Enum):
    environmental_anomaly = "environmental_anomaly"
    network_outage = "network_outage"
    power_failure = "power_failure"
    medical = "medical"
    fire_smoke = "fire_smoke"
    security = "security"
    crowd_safety = "crowd_safety"
    structural = "structural"
    weather = "weather"
    hazmat = "hazmat"
    drill_or_control = "drill_or_control"
    unknown = "unknown"


Action = Literal["contain", "evacuate", "observe"]
ACTIONS: tuple[str, ...] = ("contain", "evacuate", "observe")


class Sighting(BaseModel):
    entity_class: str = ""
    confidence: float = 0.0
    witness_count: int = 0


class SectorReport(BaseModel):
    sector: str
    sensors: dict[str, float] = Field(default_factory=dict)
    sighting: Sighting | None = None
    civilian_status: str = "unknown"
    containment_state: str = "unknown"


class TickSnapshot(BaseModel):
    tick_id: int | None = None
    sector_reports: list[SectorReport] = Field(default_factory=list)


class Incident(BaseModel):
    """One unit of work for the swarm: an unstructured log plus whatever structure we could recover."""

    incident_id: str
    source: Literal["fixture", "feed", "text", "webhook", "replay"] = "text"
    facility_id: str = "unknown-facility"
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    narrative: str
    sector_reports: list[SectorReport] = Field(default_factory=list)
    tick_id: int | None = None
    scenario: str | None = None
    severity_hint: str | None = None
    budget: dict[str, Any] | None = None
    escalation_channel: dict[str, Any] | None = None
    history: list[TickSnapshot] = Field(default_factory=list)
    expected_action: str | None = None  # only set on fixtures, used by the eval harness

    def sectors(self) -> list[str]:
        return [r.sector for r in self.sector_reports]


class SignatureMatch(BaseModel):
    label: str
    score: float
    description: str = ""


class Classification(BaseModel):
    incident_class: IncidentClass
    severity: int = Field(ge=1, le=5)
    people_at_risk: int = 0
    affected_sectors: list[str] = Field(default_factory=list)
    summary: str
    is_control: bool = False
    signature: SignatureMatch | None = None
    latency_ms: int = 0


class Source(BaseModel):
    id: str
    title: str
    url: str
    snippet: str = ""
    kind: Literal["web", "page", "research", "feed", "facility"] = "web"


class Component(BaseModel):
    component: str
    props: dict[str, Any]
    key: str = ""
    owner: str = "system"
    order: int = 0
    ts: float = Field(default_factory=time.time)

    def model_post_init(self, __context: Any) -> None:
        if not self.key:
            self.key = f"{self.owner}:{self.component}"


class SpecialistReport(BaseModel):
    agent: str
    findings: str = ""
    components: list[Component] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    tool_calls: int = 0
    latency_ms: int = 0
    error: str | None = None


class RunbookStep(BaseModel):
    id: str
    text: str
    owner: str = "incident_commander"
    source_id: str = ""


class Alternative(BaseModel):
    action: Action
    why_not: str


class Decision(BaseModel):
    action: Action
    confidence: float = Field(ge=0, le=1)
    rationale: str
    dispatch_message: str
    runbook: list[RunbookStep] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    latency_ms: int = 0
    guardrail_note: str | None = None


class ActionResult(BaseModel):
    kind: Literal["discord", "feed_submit", "one_memory", "one_action"]
    ok: bool
    dry_run: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


class RunTimings(BaseModel):
    started: float = Field(default_factory=time.time)
    first_component_ms: int | None = None
    classified_ms: int | None = None
    specialists_ms: int | None = None
    decided_ms: int | None = None
    total_ms: int | None = None

    def mark(self) -> int:
        return int((time.time() - self.started) * 1000)


RunStatus = Literal[
    "queued", "classifying", "specialists", "deciding", "awaiting_operator", "acting", "done", "failed"
]


class IncidentRun(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    incident: Incident
    classification: Classification | None = None
    reports: list[SpecialistReport] = Field(default_factory=list)
    decision: Decision | None = None
    components: list[Component] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    actions: list[ActionResult] = Field(default_factory=list)
    status: RunStatus = "queued"
    timings: RunTimings = Field(default_factory=RunTimings)
    error: str | None = None
    selected_option_ids: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    grounding_calls: int = 0

    def upsert_component(self, comp: Component) -> None:
        for i, existing in enumerate(self.components):
            if existing.key == comp.key:
                self.components[i] = comp
                return
        self.components.append(comp)
