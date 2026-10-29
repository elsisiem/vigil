"""CrewAI tools, built per run so each tool is bound to the run's source ledger.

Partial knowledge is enforced here: the sensor analyst gets sensor tools, the facility lead gets
the building graph, the field researcher gets You.com. Nobody gets everything.
"""

from __future__ import annotations

import json
from typing import Any

from crewai.tools import BaseTool, tool

from ..grounding.youcom import get_you
from .context import RunContext
from .signatures import get_signatures

RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string"},
                    "why": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["action", "why", "citations"],
            },
        },
        "hazards": {"type": "array", "items": {"type": "string"}},
        "do_not": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["actions", "hazards", "do_not"],
}


def _preview(text: str, n: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def make_sensor_tools(ctx: RunContext) -> list[BaseTool]:
    @tool("signature_match")
    def signature_match(description: str) -> str:
        """Compare a plain-language description of the current readings against the hazard signature library. Returns the three closest signatures with similarity scores, their incident class and the response they usually call for."""
        matches = get_signatures().match(description, top_k=3)
        ctx.emit("agent.tool", agent="sensors", tool="signature_match", input=_preview(description), output=_preview(json.dumps(matches)))
        return json.dumps(matches)

    @tool("sensor_trend")
    def sensor_trend(sector: str, metric: str) -> str:
        """Return the values of one metric in one sector across all ticks seen so far, oldest first, with the direction of change."""
        inc = ctx.run.incident
        points: list[tuple[str, float]] = []
        for h in inc.history:
            for r in h.sector_reports:
                if r.sector == sector and metric in r.sensors:
                    points.append((f"tick {h.tick_id}", r.sensors[metric]))
        for r in inc.sector_reports:
            if r.sector == sector and metric in r.sensors:
                points.append((f"tick {inc.tick_id}", r.sensors[metric]))
        if not points:
            out = {"sector": sector, "metric": metric, "points": [], "direction": "unknown", "note": "no readings for that sector/metric"}
        else:
            vals = [v for _, v in points]
            direction = "rising" if vals[-1] > vals[0] else "falling" if vals[-1] < vals[0] else "flat"
            out = {"sector": sector, "metric": metric, "points": [{"t": t, "v": v} for t, v in points], "direction": direction}
        ctx.emit("agent.tool", agent="sensors", tool="sensor_trend", input=f"{sector}/{metric}", output=_preview(json.dumps(out)))
        return json.dumps(out)

    @tool("current_readings")
    def current_readings() -> str:
        """Return every sensor reading and witness observation for the current tick, by sector."""
        out = ctx.sensor_view()
        if not ctx.run.incident.history:
            out += "\nNo previous ticks: this is the first reading, so no trends can be computed."
        ctx.emit("agent.tool", agent="sensors", tool="current_readings", input="", output=_preview(out))
        return out

    tools: list[BaseTool] = [current_readings, signature_match]
    if ctx.run.incident.history:
        tools.append(sensor_trend)
    return tools


def make_facility_tools(ctx: RunContext) -> list[BaseTool]:
    you = get_you()

    @tool("facility_graph")
    def facility_graph() -> str:
        """Return the floor plan as a list of sectors with capacities, exits and adjacency, plus each sector's current people and containment status."""
        out = ctx.facility_view()
        ctx.emit("agent.tool", agent="facility", tool="facility_graph", input="", output=_preview(out))
        return out

    @tool("route_to_exit")
    def route_to_exit(from_sector: str, avoid_sectors: str = "") -> str:
        """Compute the shortest route from a sector to the nearest exit, avoiding a comma-separated list of sectors. Returns the ordered sectors or an empty list if no safe route exists."""
        avoid = [s.strip() for s in avoid_sectors.split(",") if s.strip()]
        path = ctx.facility.route(from_sector, avoid=avoid)
        out = {"from": from_sector, "avoid": avoid, "route": path, "exit": path[-1] if path else None}
        ctx.emit("agent.tool", agent="facility", tool="route_to_exit", input=f"{from_sector} avoiding {avoid}", output=json.dumps(out))
        return json.dumps(out)

    @tool("venue_lookup")
    def venue_lookup(question: str) -> str:
        """Look up a fact about the venue or its surroundings on the live web (address, building, floors, nearest hospital or fire station, access). Returns passages with source ids you must cite as [S#]."""
        q = f"{ctx.facility.name} {ctx.facility.address} {question}"
        results = you.search(q, count=4, highlights=True)
        lines = []
        for r in results:
            if not r.get("url"):
                continue
            sid = ctx.add_source(title=r["title"], url=r["url"], snippet=r["snippet"], kind="web")
            lines.append(f"[{sid}] {r['title']}: {r['snippet'][:400]}")
        out = "\n".join(lines) or "No results."
        ctx.emit("agent.tool", agent="facility", tool="venue_lookup", input=_preview(question), output=_preview(out))
        return out

    return [facility_graph, route_to_exit, venue_lookup]


def make_research_tools(ctx: RunContext) -> list[BaseTool]:
    you = get_you()

    @tool("web_search")
    def web_search(query: str, freshness: str = "") -> str:
        """Search the live web for guidance, regulations or current conditions. freshness may be 'day', 'week', 'month' or empty. Returns passages with source ids you must cite as [S#]."""
        results = you.search(query, count=5, highlights=True, freshness=freshness or None)
        lines = []
        for r in results:
            if not r.get("url"):
                continue
            sid = ctx.add_source(title=r["title"], url=r["url"], snippet=r["snippet"], kind="web")
            age = f" ({r['page_age'][:10]})" if r.get("page_age") else ""
            lines.append(f"[{sid}] {r['title']}{age}: {r['snippet'][:400]}")
        out = "\n".join(lines) or "No results."
        ctx.emit("agent.tool", agent="research", tool="web_search", input=_preview(query), output=_preview(out))
        return out

    @tool("read_page")
    def read_page(url: str) -> str:
        """Read a web page you already found and return its main text (first few thousand characters), registered as a source."""
        pages = you.contents([url], max_chars=4000)
        if not pages:
            return "Could not read that page."
        page = pages[0]
        sid = ctx.add_source(title=page["title"], url=page["url"], snippet=page["markdown"][:600], kind="page")
        out = f"[{sid}] {page['title']}\n{page['markdown']}"
        ctx.emit("agent.tool", agent="research", tool="read_page", input=url, output=_preview(out))
        return out

    @tool("research_brief")
    def research_brief(question: str) -> str:
        """Run a cited research pass on an operational question (one call, about ten seconds). Returns recommended actions, hazards and things not to do, each with source ids."""
        res = you.research(question, effort="lite", output_schema=RESEARCH_SCHEMA)
        id_map: dict[int, str] = {}
        for s in res["sources"]:
            if s.get("url"):
                id_map[s["citation_index"]] = ctx.add_source(title=s["title"], url=s["url"], snippet=s["snippet"], kind="research")
        content = res.get("content") or {}
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = {"actions": [], "hazards": [content[:500]], "do_not": []}
        lines = ["Recommended actions:"]
        for a in content.get("actions", []):
            cites = " ".join(f"[{id_map[c]}]" for c in a.get("citations", []) if c in id_map)
            lines.append(f"- {a.get('action')} — {a.get('why')} {cites}".rstrip())
        if content.get("hazards"):
            lines.append("Hazards: " + "; ".join(content["hazards"]))
        if content.get("do_not"):
            lines.append("Do not: " + "; ".join(content["do_not"]))
        lines.append("Sources: " + ", ".join(f"[{v}]" for v in id_map.values()))
        out = "\n".join(lines)
        ctx.emit("agent.tool", agent="research", tool="research_brief", input=_preview(question), output=_preview(out, 300))
        return out

    @tool("local_conditions")
    def local_conditions(topic: str) -> str:
        """Check live conditions around the venue right now: 'weather', 'power outage', 'transit', 'emergency services', 'news'. Returns fresh passages with source ids."""
        q = f"{topic} San Francisco Financial District {ctx.facility.address.split(',')[0]} today"
        results = you.search(q, count=4, highlights=True, freshness="day")
        if not results:
            results = you.search(q, count=4, highlights=True, freshness="week")
        lines = []
        for r in results:
            if not r.get("url"):
                continue
            sid = ctx.add_source(title=r["title"], url=r["url"], snippet=r["snippet"], kind="web")
            lines.append(f"[{sid}] {r['title']}: {r['snippet'][:300]}")
        out = "\n".join(lines) or "No fresh results."
        ctx.emit("agent.tool", agent="research", tool="local_conditions", input=topic, output=_preview(out))
        return out

    return [web_search, read_page, research_brief, local_conditions]
