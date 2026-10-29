"""CrewAI unified Memory wired to Parasail.

Memory is what turns three agents into a swarm with *partial knowledge*: each specialist writes
to its own scope and the commander reads a read-only slice across them. Memory also persists
across runs, so the second incident at a facility benefits from the first.
"""

from __future__ import annotations

from pathlib import Path

from crewai import LLM

from ..config import ROOT, settings
from ..inference.parasail import get_parasail, thinking_params

MEMORY_DIR = ROOT / "cache" / "memory"


def parasail_llm(model: str | None = None, *, temperature: float = 0.1, max_tokens: int = 1200, deliberate: bool = False) -> LLM:
    """A CrewAI LLM pointed at Parasail's OpenAI-compatible endpoint."""
    model = model or settings.model_fast
    kwargs = {}
    extra = thinking_params(model, deliberate)
    if extra:
        kwargs["extra_body"] = extra
    return LLM(
        model=f"openai/{model}",
        base_url=settings.parasail_base_url,
        api_key=settings.parasail_api_key or "offline",
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


def _embedder(texts):  # type: ignore[no-untyped-def]
    return get_parasail().embed_sync(list(texts))


_memory = None


def get_memory(path: Path | None = None):
    """Process-wide Memory instance (LanceDB on disk under cache/memory)."""
    global _memory
    if _memory is None:
        from crewai.memory.unified_memory import Memory

        target = path or MEMORY_DIR
        target.mkdir(parents=True, exist_ok=True)
        _memory = Memory(llm=parasail_llm(), embedder=_embedder, storage=str(target))
    return _memory


def scope_for(run_id: str, agent: str) -> str:
    return f"/incident/{run_id}/{agent}"


def facility_scope(facility_id: str) -> str:
    return f"/facility/{facility_id}"
