"""Command line: run an incident, play the live feed, serve the station, evaluate, replay."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .config import settings


def _print_event(ev: dict[str, Any]) -> None:
    t = ev.get("t", 0)
    kind = ev.get("type")
    if kind == "component":
        c = ev["component"]
        print(f"[{t:6.2f}s] component  {c['owner']:>9} → {c['component']}")
    elif kind == "agent.status":
        extra = f" ({ev.get('latency_ms')} ms, {ev.get('tool_calls')} tool calls)" if ev.get("state") == "done" else ""
        print(f"[{t:6.2f}s] agent      {ev['agent']:>9} {ev['state']}{extra}")
    elif kind == "agent.tool":
        print(f"[{t:6.2f}s] tool       {ev['agent']:>9} {ev['tool']}({ev.get('input','')[:60]})")
    elif kind == "decision":
        d = ev["decision"]
        print(f"[{t:6.2f}s] DECISION   {d['action']} (confidence {d['confidence']:.2f}) — {d['rationale'][:160]}")
        print(f"           dispatch: {d['dispatch_message'][:200]}")
    elif kind == "action":
        r = ev["result"]
        print(f"[{t:6.2f}s] action     {r['kind']} ok={r['ok']} dry_run={r['dry_run']} {json.dumps(r['detail'])[:220]}")
    elif kind == "classification":
        c = ev["classification"]
        print(f"[{t:6.2f}s] classified {c['incident_class']} sev {c['severity']} people {c['people_at_risk']} control={c['is_control']}")
    elif kind == "log":
        print(f"[{t:6.2f}s] {ev.get('level','info'):5} {ev['text']}")
    elif kind == "metrics":
        print(f"[{t:6.2f}s] metrics    {json.dumps({k: v for k, v in ev.items() if k in ('timings','llm_calls','sources','components','invalid_components')})}")
    elif kind in ("run.status", "compile.done", "component.invalid"):
        print(f"[{t:6.2f}s] {kind:10} {json.dumps({k: v for k, v in ev.items() if k not in ('type','run_id','t')})[:160]}")


async def _run_fixture(name: str, tick_index: int | None, auto_act: bool, record: bool, team: str | None) -> None:
    from .ingest.fixtures import load_fixture
    from .recorder import save
    from .swarm.flow import run_incident

    incidents = load_fixture(name)
    chosen = incidents if tick_index is None else [incidents[tick_index]]
    for inc in chosen:
        print(f"\n=== {name} tick {inc.tick_id} (expected {inc.expected_action}) ===")
        ctx = await run_incident(inc, emit=_print_event, team=team, auto_act=auto_act)
        if record:
            path = save(ctx.events, f"{name}-tick-{inc.tick_id}")
            print(f"recorded → {path}")
        if ctx.run.decision and inc.expected_action:
            print(f"result: {ctx.run.decision.action} vs expected {inc.expected_action} → {'OK' if ctx.run.decision.action == inc.expected_action else 'MISS'}")


async def _run_text(text: str, auto_act: bool, team: str | None) -> None:
    import uuid

    from .models import Incident
    from .swarm.flow import run_incident

    inc = Incident(incident_id=f"manual-{uuid.uuid4().hex[:6]}", source="text", facility_id="aws-builder-loft-sf", narrative=text)
    await run_incident(inc, emit=_print_event, team=team, auto_act=auto_act)


async def _run_feed(scenario: str, team: str | None, auto_act: bool, record: bool, max_ticks: int) -> None:
    from .ingest.feed import ScenarioFeed, tick_to_incident
    from .models import TickSnapshot
    from .recorder import save
    from .swarm.flow import run_incident

    feed = ScenarioFeed(team=team)
    history: list[TickSnapshot] = []
    for i in range(max_ticks):
        tick = await feed.aget_tick(scenario)
        if tick.get("exhausted") and i > 0:
            print("feed exhausted")
            break
        inc = tick_to_incident(tick, scenario=scenario, source="feed", history=list(history))
        print(f"\n=== feed {scenario} tick {inc.tick_id} (team {feed.team}) ===")
        ctx = await run_incident(inc, emit=_print_event, team=feed.team, auto_act=auto_act)
        history.append(TickSnapshot(tick_id=inc.tick_id, sector_reports=inc.sector_reports))
        if record:
            save(ctx.events, f"feed-{scenario}-tick-{inc.tick_id}")
        if tick.get("exhausted"):
            break
    board = await feed.ascoreboard()
    print("\nscoreboard:", json.dumps(board)[:600])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vigil", description="Incident command that builds its own interface.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run one fixture (or raw text) through the swarm and print the event stream.")
    p_run.add_argument("--fixture", help="fixture name, e.g. kitchen-smoke or organizers/outbreak-level-5")
    p_run.add_argument("--tick", type=int, help="0-based tick index within the fixture (default: all)")
    p_run.add_argument("--text", help="raw incident log instead of a fixture")
    p_run.add_argument("--act", action="store_true", help="perform actions automatically (dispatch, feed, after-action)")
    p_run.add_argument("--record", action="store_true", help="save the event stream to recordings/")
    p_run.add_argument("--team", help="team handle override")

    p_feed = sub.add_parser("feed", help="Pull ticks from the live scenario feed, decide, dispatch and submit.")
    p_feed.add_argument("--scenario", default="outbreak-level-5", choices=["outbreak-level-5", "control-tick"])
    p_feed.add_argument("--team", help="team handle override (use a scratch handle while testing)")
    p_feed.add_argument("--act", action="store_true", help="dispatch to Discord and submit to the feed")
    p_feed.add_argument("--record", action="store_true")
    p_feed.add_argument("--max-ticks", type=int, default=3)

    p_serve = sub.add_parser("serve", help="Serve the command station.")
    p_serve.add_argument("--host", default=settings.host)
    p_serve.add_argument("--port", type=int, default=settings.port)
    p_serve.add_argument("--reload", action="store_true")

    sub.add_parser("eval", help="Run every fixture and report decision accuracy, latency and grounding.")
    p_replay = sub.add_parser("replay", help="Print a recorded run.")
    p_replay.add_argument("name")
    sub.add_parser("scoreboard", help="Print the live feed scoreboard.")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        if args.text:
            asyncio.run(_run_text(args.text, args.act, args.team))
        elif args.fixture:
            asyncio.run(_run_fixture(args.fixture, args.tick, args.act, args.record, args.team))
        else:
            parser.error("run needs --fixture or --text")
    elif args.cmd == "feed":
        asyncio.run(_run_feed(args.scenario, args.team, args.act, args.record, args.max_ticks))
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("vigil.server.app:app", host=args.host, port=args.port, reload=args.reload, log_level="info")
    elif args.cmd == "eval":
        from .eval.run_eval import main as eval_main

        return eval_main()
    elif args.cmd == "replay":
        from .recorder import load

        for ev in load(args.name):
            _print_event(ev)
    elif args.cmd == "scoreboard":
        from .ingest.feed import ScenarioFeed

        print(json.dumps(ScenarioFeed().scoreboard(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
