"""Runtime settings. Everything comes from the environment (.env is loaded once)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    ydc_api_key: str = field(default_factory=lambda: os.environ.get("YDC_API_KEY", ""))
    parasail_api_key: str = field(default_factory=lambda: os.environ.get("PARASAIL_API_KEY", ""))
    parasail_base_url: str = field(
        default_factory=lambda: os.environ.get("PARASAIL_BASE_URL", "https://api.parasail.io/v1")
    )
    one_secret: str = field(default_factory=lambda: os.environ.get("ONE_SECRET", ""))
    team_handle: str = field(default_factory=lambda: os.environ.get("TEAM_HANDLE", "vigil-dev"))
    scenario_mcp: str = field(
        default_factory=lambda: os.environ.get("SCENARIO_MCP", "https://api.you.com/mcp/halloween-hack-26")
    )
    discord_webhook_url: str = field(default_factory=lambda: os.environ.get("DISCORD_WEBHOOK_URL", ""))
    discord_channel_id: str = field(default_factory=lambda: os.environ.get("DISCORD_CHANNEL_ID", ""))
    # Behaviour flags
    dry_run: bool = field(default_factory=lambda: _flag("VIGIL_DRY_RUN", True))
    offline: bool = field(default_factory=lambda: _flag("VIGIL_OFFLINE", False))
    deliberate: bool = field(default_factory=lambda: _flag("VIGIL_DELIBERATE", False))
    cache_path: Path = field(
        default_factory=lambda: Path(os.environ.get("VIGIL_CACHE", str(ROOT / "cache" / "vigil-cache.sqlite")))
    )
    # Models (all served by Parasail)
    model_fast: str = field(default_factory=lambda: os.environ.get("VIGIL_MODEL_FAST", "parasail-deepseek-v41-flash"))
    model_agent: str = field(default_factory=lambda: os.environ.get("VIGIL_MODEL_AGENT", "parasail-minimax-m31-flash"))
    model_commander: str = field(default_factory=lambda: os.environ.get("VIGIL_MODEL_COMMANDER", "parasail-kimi-k3"))
    model_embed: str = field(default_factory=lambda: os.environ.get("VIGIL_MODEL_EMBED", "parasail-bge-m3"))
    host: str = field(default_factory=lambda: os.environ.get("VIGIL_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("VIGIL_PORT", "8787")))

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                "Missing settings: " + ", ".join(missing) + ". Copy .env.example to .env and fill them in."
            )


settings = Settings()
