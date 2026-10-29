"""The component catalog.

Every UI component the station can render is declared here as a Pydantic props model. The
swarm never writes HTML: each specialist is handed the JSON schema of *its own slice* of this
catalog and Parasail's constrained decoding guarantees the emitted components fit it. The
frontend holds a matching registry and renders whatever arrives, in the order it arrives.

Sources are deliberately NOT a model-emitted component: they are assembled by the system from
the grounding calls that actually happened, so a citation can never be invented.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import Component

Status = Literal["ok", "warn", "danger"]


# ----------------------------------------------------------------------------- sensors
class Reading(BaseModel):
    metric: str = Field(description="Short metric name, e.g. temperature, emf, motion, network")
    value: float
    unit: str = Field(description="Unit, e.g. C, mG, events/min, %. Use 'state' for booleans")
    status: Status
    note: str = Field(description="One short clause on why this reading matters, or empty")


class SensorPanelProps(BaseModel):
    """Current readings for one sector. Emit one per sector that has sensor data."""

    sector: str
    readings: list[Reading]


class TrendPoint(BaseModel):
    t: str = Field(description="Label for the time step, e.g. tick 142 or 14:05")
    v: float


class TrendTimelineProps(BaseModel):
    """How a metric moved across the ticks seen so far. Only emit when at least two points exist."""

    sector: str
    metric: str
    unit: str
    points: list[TrendPoint]
    direction: Literal["rising", "falling", "flat"]
    note: str


class AnomalyAssessmentProps(BaseModel):
    """What the sensor pattern most resembles, with a 0-1 anomaly score."""

    score: float = Field(description="0 = nominal, 1 = extreme anomaly")
    matched_signature: str = Field(description="Name of the closest known hazard signature")
    confidence: float = Field(description="0-1 confidence in the match")
    rationale: str = Field(description="Two sentences max, concrete, cites the readings")


# ----------------------------------------------------------------------------- facility
class RouteProps(BaseModel):
    from_sector: str
    to: str = Field(description="Exit or safe sector name")
    via: list[str] = Field(description="Ordered sectors to pass through, may be empty")
    avoid: list[str] = Field(description="Sectors to avoid, may be empty")
    note: str


class FacilityMapProps(BaseModel):
    """Which sectors to highlight, isolate and treat as safe on the floor plan."""

    highlight_sectors: list[str]
    isolated_sectors: list[str]
    safe_sectors: list[str]
    caption: str


class PeopleGroup(BaseModel):
    sector: str
    count: int
    status: str = Field(description="e.g. trapped, pinned, evacuating, clear, injured")
    action: str = Field(description="What should happen to this group next")


class PeopleAtRiskProps(BaseModel):
    """Everyone whose safety is affected, grouped by sector."""

    groups: list[PeopleGroup]
    total_at_risk: int


class EvacuationRouteProps(BaseModel):
    """Only emit when people must move. One route per affected group."""

    routes: list[RouteProps]


# ----------------------------------------------------------------------------- research
class ResponseOption(BaseModel):
    id: str = Field(description="snake_case identifier")
    label: str = Field(description="Imperative, under 8 words")
    rationale: str = Field(description="Why this applies here, one or two sentences")
    source_ids: list[str] = Field(description="IDs of sources (S1, S2...) that support this; empty if none")
    risk: Literal["low", "medium", "high"]
    recommended: bool


class ResponseOptionsProps(BaseModel):
    """Grounded response options the operator can arm. 3 to 6 options."""

    options: list[ResponseOption]


class ContextItem(BaseModel):
    kind: Literal["weather", "outage", "emergency_services", "news", "regulation", "facility", "other"]
    title: str
    value: str = Field(description="The fact itself, one line")
    source_id: str = Field(description="Supporting source ID or empty")


class ExternalContextProps(BaseModel):
    """Live facts from outside the building that change the response: weather, outages, nearest services, rules."""

    items: list[ContextItem]


# ----------------------------------------------------------------------------- command
class AlternativeProps(BaseModel):
    action: Literal["contain", "evacuate", "observe"]
    why_not: str


class DecisionPanelProps(BaseModel):
    """The commander's call. Exactly one per incident."""

    action: Literal["contain", "evacuate", "observe"]
    confidence: float
    rationale: str = Field(description="Three sentences max, references specialist findings")
    alternatives: list[AlternativeProps]


class DispatchComposerProps(BaseModel):
    """The escalation message to post. Must start with the team handle given in context."""

    channel: str
    message: str
    requires_approval: bool


class RunbookStepProps(BaseModel):
    id: str
    text: str = Field(description="Imperative, concrete, one action")
    owner: str = Field(description="Role: incident_commander, floor_lead, facilities, medical, comms")
    source_id: str = Field(description="Supporting source ID or empty")


class RunbookProps(BaseModel):
    """Ordered checklist for the next ten minutes. 4 to 8 steps."""

    steps: list[RunbookStepProps]


# ----------------------------------------------------------------------------- system-only
class IncidentHeaderProps(BaseModel):
    incident_id: str
    title: str
    facility: str
    incident_class: str
    severity: int
    source: str
    received_at: str


class SeverityBannerProps(BaseModel):
    level: int
    label: str
    people_at_risk: int
    is_control: bool


class SourcesProps(BaseModel):
    sources: list[dict[str, Any]]


class MonitorPanelProps(BaseModel):
    message: str
    next_check: str


CATALOG: dict[str, type[BaseModel]] = {
    # specialist: sensors
    "SensorPanel": SensorPanelProps,
    "TrendTimeline": TrendTimelineProps,
    "AnomalyAssessment": AnomalyAssessmentProps,
    # specialist: facility
    "FacilityMap": FacilityMapProps,
    "PeopleAtRisk": PeopleAtRiskProps,
    "EvacuationRoute": EvacuationRouteProps,
    # specialist: research
    "ResponseOptions": ResponseOptionsProps,
    "ExternalContext": ExternalContextProps,
    # commander
    "DecisionPanel": DecisionPanelProps,
    "DispatchComposer": DispatchComposerProps,
    "Runbook": RunbookProps,
    # system
    "IncidentHeader": IncidentHeaderProps,
    "SeverityBanner": SeverityBannerProps,
    "Sources": SourcesProps,
    "MonitorPanel": MonitorPanelProps,
}

OWNERS: dict[str, list[str]] = {
    "sensors": ["SensorPanel", "TrendTimeline", "AnomalyAssessment"],
    "facility": ["FacilityMap", "PeopleAtRisk", "EvacuationRoute"],
    "research": ["ResponseOptions", "ExternalContext"],
    "commander": ["DecisionPanel", "DispatchComposer", "Runbook"],
}

# Components that may legitimately appear more than once per owner (keyed by a prop).
MULTI_KEY: dict[str, str] = {"SensorPanel": "sector", "TrendTimeline": "metric"}


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local $ref/$defs so the schema is self-contained for constrained decoding."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return walk(copy.deepcopy(defs[ref]))
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic schema acceptable to strict constrained decoding.

    Every object gets additionalProperties:false and all properties required; titles and
    defaults are dropped because they only add tokens.
    """
    schema = _inline_refs(copy.deepcopy(schema))

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for k, v in list(node.items()):
                node[k] = walk(v)
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def component_schema(name: str) -> dict[str, Any]:
    props = strictify(CATALOG[name].model_json_schema())
    doc = (CATALOG[name].__doc__ or "").strip()
    return {
        "type": "object",
        "description": doc,
        "properties": {
            "component": {"type": "string", "enum": [name]},
            "props": props,
        },
        "required": ["component", "props"],
        "additionalProperties": False,
    }


def components_schema(names: list[str]) -> dict[str, Any]:
    """Schema for `{"components": [ ...any of the named components... ]}`."""
    return {
        "type": "object",
        "properties": {
            "components": {
                "type": "array",
                "items": {"anyOf": [component_schema(n) for n in names]},
            }
        },
        "required": ["components"],
        "additionalProperties": False,
    }


def catalog_brief(names: list[str]) -> str:
    """Human-readable description of a slice of the catalog, for prompts."""
    lines = []
    for n in names:
        doc = CATALOG[n].__doc__ or ""
        first = doc.strip().splitlines()[0] if doc.strip() else ""
        lines.append(f"- {n}: {first}")
    return "\n".join(lines)


def validate_component(raw: dict[str, Any], owner: str, order: int) -> Component:
    name = raw.get("component")
    if name not in CATALOG:
        raise ValueError(f"unknown component {name!r}")
    props = CATALOG[name].model_validate(raw.get("props", {})).model_dump()
    key = f"{owner}:{name}"
    if name in MULTI_KEY and MULTI_KEY[name] in props:
        key += f":{props[MULTI_KEY[name]]}"
    return Component(component=name, props=props, owner=owner, order=order, key=key)
