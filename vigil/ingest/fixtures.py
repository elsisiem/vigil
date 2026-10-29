"""Fixture loading.

Two shapes are supported:
  * the organizers' files (fixtures/organizers/*.json): ticks with sector_reports, or a single
    raw_incident_stream narrative;
  * Vigil's own files (fixtures/*.json): {"name", "facility_id", "description", "ticks": [...]}
    where each tick has a narrative, optional sector_reports and an expected_action label used by
    the eval harness.

A fixture with several ticks yields several Incidents, each carrying the history of the ticks
before it, exactly like consecutive pulls from the live feed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import ROOT
from ..models import Incident, SectorReport, TickSnapshot
from .feed import tick_to_incident

FIXTURE_DIR = ROOT / "fixtures"

# Labels for the organizers' fixtures. These are our reading of the brief's scoring curve
# (observe on a control tick; move people when a sector is breached with people pinned).
ORGANIZER_EXPECTED: dict[str, dict[str, str]] = {
    "outbreak-level-5": {"142": "evacuate", "143": "evacuate"},
    "control-tick": {"900": "observe"},
    "haunted-dashboard": {"spooky-2026-9912": "evacuate"},
}


def list_fixtures() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "name": path.stem,
                "group": "vigil",
                "description": data.get("description", ""),
                "ticks": len(data.get("ticks", [])),
                "facility_id": data.get("facility_id", ""),
            }
        )
    for path in sorted((FIXTURE_DIR / "organizers").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "name": f"organizers/{path.stem}",
                "group": "organizers",
                "description": f"Organizer fixture: {data.get('scenario', path.stem)}",
                "ticks": len(data.get("ticks", [])),
                "facility_id": "aws-builder-loft-sf",
            }
        )
    return out


def _snapshot(inc: Incident) -> TickSnapshot:
    return TickSnapshot(tick_id=inc.tick_id, sector_reports=inc.sector_reports)


def load_fixture(name: str) -> list[Incident]:
    if name.startswith("organizers/"):
        path = FIXTURE_DIR / "organizers" / f"{name.split('/', 1)[1]}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario = data.get("scenario", path.stem)
        expected = ORGANIZER_EXPECTED.get(scenario, {})
        incidents: list[Incident] = []
        history: list[TickSnapshot] = []
        for tick in data.get("ticks", []):
            key = str(tick.get("tick_id") or tick.get("incident_id"))
            inc = tick_to_incident(
                tick, scenario=scenario, source="fixture", history=list(history), expected_action=expected.get(key)
            )
            incidents.append(inc)
            history.append(_snapshot(inc))
        return incidents

    path = FIXTURE_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    facility_id = data.get("facility_id", "unknown-facility")
    incidents = []
    history = []
    for i, tick in enumerate(data.get("ticks", [])):
        reports = [SectorReport(**r) for r in tick.get("sector_reports", [])]
        inc = Incident(
            incident_id=tick.get("incident_id", f"{name}-{i + 1}"),
            source="fixture",
            facility_id=facility_id,
            narrative=tick["narrative"],
            sector_reports=reports,
            tick_id=tick.get("tick_id", i + 1),
            scenario=name,
            severity_hint=tick.get("severity_hint"),
            history=list(history),
            expected_action=tick.get("expected_action"),
        )
        incidents.append(inc)
        history.append(_snapshot(inc))
    return incidents


def load_all() -> dict[str, list[Incident]]:
    return {f["name"]: load_fixture(f["name"]) for f in list_fixtures()}
