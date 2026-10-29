"""Per-run context shared by the flow, the agents' tools and the server.

It owns the source ledger (so every citation maps to a real grounding call), the component
list, and a thread-safe emitter that the server turns into WebSocket events and the recorder
turns into a replayable file.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ..facility import Facility, get_facility
from ..models import Component, Incident, IncidentRun, Source

Emitter = Callable[[dict[str, Any]], None]


class RunContext:
    def __init__(self, incident: Incident, emit: Emitter | None = None, *, team: str, auto_act: bool = False):
        self.run = IncidentRun(incident=incident)
        self.team = team
        self.auto_act = auto_act
        self._emit = emit or (lambda e: None)
        self._lock = threading.Lock()
        self._sources_by_url: dict[str, Source] = {}
        self._order = 0
        self.events: list[dict[str, Any]] = []
        self.facility: Facility = get_facility(incident.facility_id, incident.sectors())
        self.invalid_components = 0
        self.agent_registry: dict[int, str] = {}

    # ------------------------------------------------------------------ events
    def emit(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type, "run_id": self.run.run_id, "t": round(time.time() - self.run.timings.started, 3), **payload}
        with self._lock:
            self.events.append(event)
        self._emit(event)

    def log(self, text: str, level: str = "info") -> None:
        self.emit("log", level=level, text=text)

    def set_status(self, status: str) -> None:
        self.run.status = status  # type: ignore[assignment]
        self.emit("run.status", status=status)

    # ------------------------------------------------------------------ sources
    def add_source(self, *, title: str, url: str, snippet: str = "", kind: str = "web") -> str:
        with self._lock:
            if url in self._sources_by_url:
                existing = self._sources_by_url[url]
                if snippet and len(snippet) > len(existing.snippet):
                    existing.snippet = snippet[:900]
                return existing.id
            sid = f"S{len(self._sources_by_url) + 1}"
            src = Source(id=sid, title=(title or url)[:160], url=url, snippet=snippet[:900], kind=kind)  # type: ignore[arg-type]
            self._sources_by_url[url] = src
            self.run.sources.append(src)
        self.run.grounding_calls += 1
        return sid

    def sources_text(self, ids: list[str] | None = None) -> str:
        rows = []
        for s in self.run.sources:
            if ids is not None and s.id not in ids:
                continue
            rows.append(f"[{s.id}] {s.title} — {s.url}\n    {s.snippet[:300]}")
        return "\n".join(rows) if rows else "(no sources yet)"

    def known_source_ids(self) -> set[str]:
        return {s.id for s in self.run.sources}

    def emit_sources(self) -> None:
        comp = Component(
            component="Sources",
            props={"sources": [s.model_dump() for s in self.run.sources]},
            owner="system",
            order=900,
            key="system:Sources",
        )
        self.add_component(comp)

    # ------------------------------------------------------------------ components
    def add_component(self, comp: Component) -> None:
        with self._lock:
            self._order += 1
            if not comp.order:
                comp.order = self._order
            self.run.upsert_component(comp)
            if self.run.timings.first_component_ms is None and comp.owner != "system":
                self.run.timings.first_component_ms = self.run.timings.mark()
        self.emit("component", component=comp.model_dump())

    def scrub_source_ids(self, props: dict[str, Any]) -> dict[str, Any]:
        """Drop citations that do not correspond to a real grounding call."""
        known = self.known_source_ids()

        def walk(node: Any) -> Any:
            if isinstance(node, dict):
                for k, v in list(node.items()):
                    if k == "source_ids" and isinstance(v, list):
                        node[k] = [x for x in v if x in known]
                    elif k == "source_id" and isinstance(v, str):
                        node[k] = v if v in known else ""
                    else:
                        node[k] = walk(v)
                return node
            if isinstance(node, list):
                return [walk(x) for x in node]
            return node

        return walk(props)

    # ------------------------------------------------------------------ views for agents (partial knowledge)
    def sensor_view(self) -> str:
        inc = self.run.incident
        lines = [f"Facility: {inc.facility_id}. Tick: {inc.tick_id}."]
        for r in inc.sector_reports:
            sens = ", ".join(f"{k}={v:g}" for k, v in r.sensors.items()) or "no sensor data"
            line = f"- {r.sector}: {sens}"
            if r.sighting and r.sighting.entity_class:
                line += f"; observation '{r.sighting.entity_class}' confidence {r.sighting.confidence:.2f}, {r.sighting.witness_count} witnesses"
            lines.append(line)
        if inc.history:
            lines.append("Previous ticks:")
            for h in inc.history:
                for r in h.sector_reports:
                    sens = ", ".join(f"{k}={v:g}" for k, v in r.sensors.items())
                    lines.append(f"  tick {h.tick_id} {r.sector}: {sens}")
        return "\n".join(lines)

    def facility_view(self) -> str:
        inc = self.run.incident
        lines = [self.facility.describe(), "", "Sector status this tick:"]
        for r in inc.sector_reports:
            lines.append(f"- {r.sector}: people '{r.civilian_status}', state '{r.containment_state}'")
        return "\n".join(lines)

    def narrative_view(self) -> str:
        inc = self.run.incident
        cls = self.run.classification
        head = f"Incident {inc.incident_id} at {self.facility.name}, {self.facility.address}."
        if cls:
            head += f" Classified as {cls.incident_class.value}, severity {cls.severity}/5, {cls.people_at_risk} people at risk. {cls.summary}"
        return head + "\n\nRaw incident log:\n" + inc.narrative
