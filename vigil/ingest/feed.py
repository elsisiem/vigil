"""Client for the organizers' scenario feed (an MCP server over streamable HTTP).

The feed is one of several incident sources the station can ingest. Ticks are converted to the
same Incident model as a pasted log or a fixture, so the swarm never knows where a tick came from.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from ..cache import get_cache
from ..config import settings
from ..models import Incident, SectorReport, Sighting, TickSnapshot


class FeedError(RuntimeError):
    pass


class ScenarioFeed:
    def __init__(self, url: str | None = None, team: str | None = None):
        self.url = url or settings.scenario_mcp
        self.team = team or settings.team_handle
        self._http = httpx.Client(timeout=30.0)
        self._id = 0
        self.cache = get_cache()

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        resp = self._http.post(
            self.url,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        resp.raise_for_status()
        text = resp.text
        data: Any = None
        if "text/event-stream" in resp.headers.get("content-type", ""):
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
        else:
            data = resp.json()
        if data is None:
            raise FeedError(f"empty response from feed for {method}")
        if "error" in data:
            raise FeedError(str(data["error"]))
        return data.get("result")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        for c in result.get("content", []) if isinstance(result, dict) else []:
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except json.JSONDecodeError:
                    return c["text"]
        return result

    # ------------------------------------------------------------------ tools
    def list_scenarios(self) -> list[dict[str, Any]]:
        return self.call_tool("hack26-list-scenarios", {}).get("scenarios", [])

    def get_tick(self, scenario: str, team: str | None = None) -> dict[str, Any]:
        tick = self.call_tool("hack26-get-tick", {"team": team or self.team, "scenario": scenario})
        self.cache.put(self.cache.key("feed.tick", {"team": team or self.team, "scenario": scenario, "t": time.time()}), tick, "feed.tick")
        return tick

    def submit_containment(self, tick_id: int, action: str, team: str | None = None) -> dict[str, Any]:
        return self.call_tool(
            "hack26-submit-containment", {"team": team or self.team, "tick_id": int(tick_id), "action": action}
        )

    def scoreboard(self) -> dict[str, Any]:
        return self.call_tool("hack26-get-scoreboard", {})

    def ask_oracle(self, question: str) -> dict[str, Any]:
        return self.call_tool("hack26-ask-oracle", {"question": question[:500]})

    # ------------------------------------------------------------------ async
    async def aget_tick(self, scenario: str, team: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.get_tick, scenario, team)

    async def asubmit_containment(self, tick_id: int, action: str, team: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.submit_containment, tick_id, action, team)

    async def ascoreboard(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.scoreboard)


# ---------------------------------------------------------------------- tick -> incident
def describe_tick(tick: dict[str, Any]) -> str:
    """Render a structured tick as the kind of log line a human dispatcher would read."""
    parts = [f"Tick {tick.get('tick_id')} from facility {tick.get('facility_id', 'unknown')}."]
    for r in tick.get("sector_reports", []) or []:
        s = r.get("sensors") or {}
        bits = []
        if "emf_milligauss" in s:
            bits.append(f"EMF {s['emf_milligauss']} mG")
        if "temp_c" in s:
            bits.append(f"temperature {s['temp_c']} C")
        if "motion_events" in s:
            bits.append(f"{s['motion_events']} motion events")
        for k, v in s.items():
            if k not in {"emf_milligauss", "temp_c", "motion_events"}:
                bits.append(f"{k} {v}")
        line = f"Sector {r.get('sector')}: " + ", ".join(bits) + "."
        sight = r.get("sighting")
        if sight:
            line += (
                f" Reported observation: {sight.get('entity_class')} "
                f"(confidence {sight.get('confidence')}, {sight.get('witness_count')} witnesses)."
            )
        line += f" People: {r.get('civilian_status', 'unknown')}. Containment state: {r.get('containment_state', 'unknown')}."
        parts.append(line)
    budget = tick.get("swarm_budget") or {}
    if budget:
        parts.append(
            f"Response budget: max {budget.get('max_active_agents')} active agents, "
            f"{budget.get('api_credits_remaining')} credits remaining."
        )
    return " ".join(parts)


def tick_to_incident(
    tick: dict[str, Any],
    *,
    scenario: str | None,
    source: str = "feed",
    history: list[TickSnapshot] | None = None,
    expected_action: str | None = None,
) -> Incident:
    reports: list[SectorReport] = []
    for r in tick.get("sector_reports", []) or []:
        sight = r.get("sighting")
        reports.append(
            SectorReport(
                sector=r.get("sector", "unknown"),
                sensors={k: float(v) for k, v in (r.get("sensors") or {}).items() if isinstance(v, (int, float))},
                sighting=Sighting(**sight) if sight else None,
                civilian_status=str(r.get("civilian_status", "unknown")),
                containment_state=str(r.get("containment_state", "unknown")),
            )
        )
    tick_id = tick.get("tick_id")
    incident_id = tick.get("incident_id") or f"{scenario or 'tick'}-{tick_id}"
    narrative = tick.get("raw_incident_stream") or describe_tick(tick)
    return Incident(
        incident_id=str(incident_id),
        source=source,  # type: ignore[arg-type]
        facility_id=tick.get("facility_id", "aws-builder-loft-sf"),
        narrative=narrative,
        sector_reports=reports,
        tick_id=tick_id,
        scenario=scenario,
        severity_hint=tick.get("severity_tier"),
        budget=tick.get("swarm_budget"),
        escalation_channel=tick.get("escalation_channel"),
        history=history or [],
        expected_action=expected_action,
    )
