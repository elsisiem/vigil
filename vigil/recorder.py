"""Record and replay runs.

Every event a run emits is appended to a JSONL file with its offset from the run start. Replay
re-emits those events with the same relative timing (scaled), which is how the station opens
and plays a full incident with no network and no keys.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from .config import ROOT

RECORDINGS = ROOT / "recordings"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def save(events: list[dict[str, Any]], name: str) -> Path:
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    path = RECORDINGS / f"{_slug(name)}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")
    return path


def list_recordings() -> list[dict[str, Any]]:
    out = []
    for p in sorted(RECORDINGS.glob("*.jsonl")):
        first: dict[str, Any] = {}
        n = 0
        with p.open(encoding="utf-8") as f:
            for line in f:
                n += 1
                if not first:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "run.created":
                        first = ev
        inc = first.get("incident", {})
        out.append({"name": p.stem, "events": n, "incident_id": inc.get("incident_id"), "source": inc.get("source"), "narrative": (inc.get("narrative") or "")[:160]})
    return out


def load(name: str) -> list[dict[str, Any]]:
    path = RECORDINGS / f"{_slug(name)}.jsonl"
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


async def replay(name: str, emit: Callable[[dict[str, Any]], None], *, speed: float = 1.0, run_id: str | None = None, max_gap: float = 2.5) -> list[dict[str, Any]]:
    events = load(name)
    last = 0.0
    for ev in events:
        gap = max(0.0, float(ev.get("t", 0)) - last)
        last = float(ev.get("t", 0))
        await asyncio.sleep(min(gap / max(speed, 0.01), max_gap))
        if run_id:
            ev = {**ev, "run_id": run_id}
        ev = {**ev, "replayed": True}
        emit(ev)
    return events
