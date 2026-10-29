"""FastAPI server: REST to start runs and act on them, one WebSocket that streams every event.

The frontend never polls. Components, agent activity, decisions and action results all arrive
over /ws as they happen, and a run can be replayed from a recording through the same socket.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..cache import get_cache
from ..config import settings
from ..facility import get_facility
from ..ingest.feed import ScenarioFeed, tick_to_incident
from ..ingest.fixtures import list_fixtures, load_fixture
from ..models import Incident, TickSnapshot
from ..recorder import list_recordings, replay, save
from ..swarm.context import RunContext
from ..swarm.flow import perform_actions, run_context

STATIC = Path(__file__).parent / "static"


class Bus:
    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=2000)
        self._lock = threading.Lock()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict[str, Any]) -> None:
        self.history.append(event)
        for q in list(self.clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self.clients.discard(q)


app = FastAPI(title="Vigil", version="0.1.0")
bus = Bus()
RUNS: dict[str, RunContext] = {}
RUN_ORDER: list[str] = []
FEED_HISTORY: dict[str, list[TickSnapshot]] = {}


@app.on_event("startup")
async def _startup() -> None:
    bus.attach(asyncio.get_running_loop())
    from ..swarm.flow import prewarm

    asyncio.create_task(prewarm())


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------------- read endpoints
@app.get("/api/health")
async def health() -> dict[str, Any]:
    from ..actions.one_client import get_one

    one_ok, one_why = await asyncio.to_thread(get_one().available)
    return {
        "ok": True,
        "team": settings.team_handle,
        "offline": settings.offline,
        "dry_run": settings.dry_run,
        "deliberate": settings.deliberate,
        "keys": {"youcom": bool(settings.ydc_api_key), "parasail": bool(settings.parasail_api_key), "one": one_ok, "one_note": one_why},
        "models": {"fast": settings.model_fast, "agent": settings.model_agent, "commander": settings.model_commander, "embed": settings.model_embed},
        "cache_entries": get_cache().count(),
        "recordings": len(list_recordings()),
    }


@app.get("/api/fixtures")
async def fixtures() -> dict[str, Any]:
    return {"fixtures": list_fixtures(), "recordings": list_recordings()}


@app.get("/api/facility/{facility_id}")
async def facility(facility_id: str) -> dict[str, Any]:
    return get_facility(facility_id).layout()


def _run_summary(ctx: RunContext) -> dict[str, Any]:
    r = ctx.run
    return {
        "run_id": r.run_id,
        "incident_id": r.incident.incident_id,
        "source": r.incident.source,
        "tick_id": r.incident.tick_id,
        "status": r.status,
        "incident_class": r.classification.incident_class.value if r.classification else None,
        "severity": r.classification.severity if r.classification else None,
        "action": r.decision.action if r.decision else None,
        "components": len(r.components),
        "timings": r.timings.model_dump(),
    }


@app.get("/api/runs")
async def runs() -> list[dict[str, Any]]:
    return [_run_summary(RUNS[rid]) for rid in RUN_ORDER if rid in RUNS]


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str) -> dict[str, Any]:
    ctx = RUNS.get(run_id)
    if not ctx:
        raise HTTPException(404, "unknown run")
    data = ctx.run.model_dump(mode="json")
    data["facility"] = ctx.facility.layout()
    data["team"] = ctx.team
    return data


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> list[dict[str, Any]]:
    ctx = RUNS.get(run_id)
    if not ctx:
        raise HTTPException(404, "unknown run")
    return ctx.events


@app.get("/api/scoreboard")
async def scoreboard() -> dict[str, Any]:
    try:
        return await ScenarioFeed().ascoreboard()
    except Exception as e:
        return {"error": str(e)[:200], "teams": []}


# ---------------------------------------------------------------------- starting runs
class StartBody(BaseModel):
    source: Literal["fixture", "text", "feed", "replay"]
    fixture: str | None = None
    tick: int | None = None
    text: str | None = None
    facility_id: str = "aws-builder-loft-sf"
    scenario: str | None = None
    team: str | None = None
    auto_act: bool = False
    recording: str | None = None
    record: bool = True


def _new_ctx(incident: Incident, team: str | None, auto_act: bool) -> RunContext:
    ctx = RunContext(incident, bus.publish, team=team or settings.team_handle, auto_act=auto_act)
    RUNS[ctx.run.run_id] = ctx
    RUN_ORDER.append(ctx.run.run_id)
    return ctx


async def _execute(ctx: RunContext, record_name: str | None) -> None:
    await run_context(ctx)
    if record_name and ctx.run.status != "failed":
        try:
            save(ctx.events, record_name)
        except OSError:
            pass


async def _execute_sequence(ctxs: list[RunContext], record_base: str | None) -> None:
    for ctx in ctxs:
        await _execute(ctx, f"{record_base}-tick-{ctx.run.incident.tick_id}" if record_base else None)


@app.post("/api/incidents")
async def start(body: StartBody) -> dict[str, Any]:
    if body.source == "replay":
        if not body.recording:
            raise HTTPException(400, "recording is required")
        run_id = uuid.uuid4().hex[:10]
        asyncio.create_task(replay(body.recording, bus.publish, run_id=run_id))
        return {"run_ids": [run_id], "replay": True}

    if body.source == "fixture":
        if not body.fixture:
            raise HTTPException(400, "fixture is required")
        incidents = load_fixture(body.fixture)
        if body.tick is not None:
            incidents = [incidents[body.tick]]
        ctxs = [_new_ctx(inc, body.team, body.auto_act) for inc in incidents]
        asyncio.create_task(_execute_sequence(ctxs, body.fixture.replace("/", "-") if body.record else None))
        return {"run_ids": [c.run.run_id for c in ctxs]}

    if body.source == "text":
        if not body.text or not body.text.strip():
            raise HTTPException(400, "text is required")
        inc = Incident(
            incident_id=f"manual-{datetime.now(timezone.utc).strftime('%H%M%S')}",
            source="text",
            facility_id=body.facility_id,
            narrative=body.text.strip(),
        )
        ctx = _new_ctx(inc, body.team, body.auto_act)
        asyncio.create_task(_execute(ctx, f"text-{inc.incident_id}" if body.record else None))
        return {"run_ids": [ctx.run.run_id]}

    # live feed: pull the next tick for this team and run it, keeping per-scenario history
    scenario = body.scenario or "outbreak-level-5"
    team = body.team or settings.team_handle
    feed = ScenarioFeed(team=team)
    tick = await feed.aget_tick(scenario)
    key = f"{team}:{scenario}"
    history = FEED_HISTORY.setdefault(key, [])
    inc = tick_to_incident(tick, scenario=scenario, source="feed", history=list(history))
    if not tick.get("exhausted"):
        history.append(TickSnapshot(tick_id=inc.tick_id, sector_reports=inc.sector_reports))
    ctx = _new_ctx(inc, team, body.auto_act)
    asyncio.create_task(_execute(ctx, f"feed-{scenario}-tick-{inc.tick_id}" if body.record else None))
    return {"run_ids": [ctx.run.run_id], "tick": tick}


# ---------------------------------------------------------------------- operating on a run
class ActBody(BaseModel):
    action: Literal["contain", "evacuate", "observe"] | None = None
    dry_run: bool | None = None


@app.post("/api/runs/{run_id}/act")
async def act(run_id: str, body: ActBody) -> dict[str, Any]:
    ctx = RUNS.get(run_id)
    if not ctx:
        raise HTTPException(404, "unknown run")
    if ctx.run.decision is None:
        raise HTTPException(409, "the swarm has not decided yet")
    results = await perform_actions(ctx, override_action=body.action, dry_run=body.dry_run)
    return {"results": [r.model_dump() for r in results]}


class SelectBody(BaseModel):
    option_ids: list[str]


@app.post("/api/runs/{run_id}/select")
async def select(run_id: str, body: SelectBody) -> dict[str, Any]:
    ctx = RUNS.get(run_id)
    if not ctx:
        raise HTTPException(404, "unknown run")
    ctx.run.selected_option_ids = body.option_ids
    ctx.emit("selection", option_ids=body.option_ids)
    return {"ok": True}


class NoteBody(BaseModel):
    note: str
    auto_act: bool = False


@app.post("/api/runs/{run_id}/recompile")
async def recompile(run_id: str, body: NoteBody) -> dict[str, Any]:
    """Operator update: fold the note into the log as a new tick and run the swarm again."""
    ctx = RUNS.get(run_id)
    if not ctx:
        raise HTTPException(404, "unknown run")
    prev = ctx.run.incident
    stamp = datetime.now(timezone.utc).strftime("%H:%M")
    selected = ""
    if ctx.run.selected_option_ids:
        selected = f" Operator armed response options: {', '.join(ctx.run.selected_option_ids)}."
    narrative = f"{prev.narrative}\n\n{stamp} Operator update: {body.note.strip()}.{selected}"
    inc = Incident(
        incident_id=prev.incident_id,
        source=prev.source if prev.source != "feed" else "text",
        facility_id=prev.facility_id,
        narrative=narrative,
        sector_reports=[],
        tick_id=(prev.tick_id or 0) + 1,
        scenario=prev.scenario,
        history=list(prev.history) + [TickSnapshot(tick_id=prev.tick_id, sector_reports=prev.sector_reports)],
    )
    new_ctx = _new_ctx(inc, ctx.team, body.auto_act)
    asyncio.create_task(_execute(new_ctx, None))
    return {"run_ids": [new_ctx.run.run_id]}


# ---------------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    q = bus.subscribe()
    try:
        await websocket.send_json({"type": "hello", "team": settings.team_handle, "dry_run": settings.dry_run, "offline": settings.offline})
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(q)
