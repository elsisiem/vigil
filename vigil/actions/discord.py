"""Escalation dispatch to the venue's Discord channel through the webhook the organizers issued.

The message is always composed from runtime state by the commander and always starts with the
team handle so the channel can attribute it. Dry-run mode logs instead of posting.
"""

from __future__ import annotations

import time

import httpx

from ..config import settings
from ..models import ActionResult


def format_dispatch(team: str, sector: str, action: str, rationale: str) -> str:
    rationale = " ".join(rationale.split())
    return f"{team} · {sector} · {action} · {rationale}"[:1900]


def dispatch(message: str, *, dry_run: bool | None = None, team: str | None = None) -> ActionResult:
    team = team or settings.team_handle
    dry = settings.dry_run if dry_run is None else dry_run
    if not message.startswith(team):
        message = f"{team} · {message}"
    message = message[:1900]
    if dry:
        return ActionResult(kind="discord", ok=True, dry_run=True, detail={"message": message, "channel": "#loft-incident"})
    if not settings.discord_webhook_url:
        return ActionResult(kind="discord", ok=False, detail={"error": "DISCORD_WEBHOOK_URL not set", "message": message})
    t0 = time.time()
    try:
        resp = httpx.post(settings.discord_webhook_url, json={"content": message}, timeout=20)
        ok = resp.status_code in (200, 204)
        return ActionResult(
            kind="discord",
            ok=ok,
            detail={
                "message": message,
                "status": resp.status_code,
                "channel": "#loft-incident",
                "latency_ms": int((time.time() - t0) * 1000),
                **({} if ok else {"error": resp.text[:300]}),
            },
        )
    except httpx.HTTPError as e:
        return ActionResult(kind="discord", ok=False, detail={"message": message, "error": str(e)})
