"""Parasail: the only model provider in the system.

Three capabilities are used and each is irreplaceable:
  * schema-locked chat completions (response_format json_schema, strict) for every decision
    and every UI manifest the swarm emits;
  * streaming of those schema-locked completions, so components render mid-generation;
  * embeddings (BGE-M3) for the hazard-signature store and CrewAI memory recall.

Every call is cached by request hash so a run can be replayed offline with identical output.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, OpenAI

from ..cache import OfflineMiss, get_cache
from ..config import settings
from .streamjson import ComponentStreamParser


def thinking_params(model: str, deliberate: bool = False) -> dict[str, Any]:
    """Per-model switches that keep latency predictable.

    The hackathon models are reasoning models. Left alone they spend their whole token budget
    thinking before emitting JSON; with these switches a schema-locked call returns in ~1-2 s.
    """
    if deliberate:
        return {}
    m = model.lower()
    if "minimax" in m:
        return {}  # adaptive thinking cannot be disabled, and the model is already fast
    if "glm" in m:
        return {"thinking": {"type": "disabled"}}
    return {"chat_template_kwargs": {"enable_thinking": False}}


@dataclass
class UsageLedger:
    calls: int = 0
    cached: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: list[int] = field(default_factory=list)
    by_model: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, model: str, usage: Any | None, latency_ms: int, cached: bool = False) -> None:
        with self._lock:
            self.calls += 1
            self.by_model[model] = self.by_model.get(model, 0) + 1
            if cached:
                self.cached += 1
                return
            self.latency_ms.append(latency_ms)
            if usage is not None:
                self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0

    def summary(self) -> dict[str, Any]:
        lat = sorted(self.latency_ms)
        return {
            "calls": self.calls,
            "cached": self.cached,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "p50_latency_ms": lat[len(lat) // 2] if lat else None,
            "max_latency_ms": lat[-1] if lat else None,
            "by_model": dict(self.by_model),
        }


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


class ParasailClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        offline: bool | None = None,
        deliberate: bool | None = None,
    ):
        self.api_key = api_key or settings.parasail_api_key
        self.base_url = base_url or settings.parasail_base_url
        self.offline = settings.offline if offline is None else offline
        self.deliberate = settings.deliberate if deliberate is None else deliberate
        self.cache = get_cache()
        self.ledger = UsageLedger()
        self._sync = OpenAI(api_key=self.api_key or "offline", base_url=self.base_url, timeout=120, max_retries=2)
        self._async = AsyncOpenAI(
            api_key=self.api_key or "offline", base_url=self.base_url, timeout=120, max_retries=2
        )

    # ------------------------------------------------------------------ request building
    def _chat_body(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any] | None,
        name: str,
        max_tokens: int,
        temperature: float,
        deliberate: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            }
        extra = thinking_params(model, deliberate or self.deliberate)
        if extra:
            body["extra_body"] = extra
        return body

    # ------------------------------------------------------------------ sync surface (tools)
    def structured_sync(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 1500,
        temperature: float = 0.2,
        deliberate: bool = False,
    ) -> dict[str, Any]:
        model = model or settings.model_fast
        body = self._chat_body(
            model=model, system=system, user=user, schema=schema, name=name,
            max_tokens=max_tokens, temperature=temperature, deliberate=deliberate,
        )
        key = self.cache.key("parasail.chat", body)
        cached = self.cache.get(key)
        if cached is not None:
            self.ledger.record(model, None, 0, cached=True)
            return cached["json"]
        if self.offline:
            raise OfflineMiss(f"parasail structured call not in cache: {name}")
        t0 = time.time()
        resp = self._sync.chat.completions.create(**body)
        latency = int((time.time() - t0) * 1000)
        data = _extract_json(resp.choices[0].message.content or "")
        self.ledger.record(model, resp.usage, latency)
        self.cache.put(key, {"json": data, "latency_ms": latency, "model": model}, "parasail.chat")
        return data

    def complete_sync(
        self, *, system: str, user: str, model: str | None = None, max_tokens: int = 700, temperature: float = 0.3
    ) -> str:
        model = model or settings.model_fast
        body = self._chat_body(
            model=model, system=system, user=user, schema=None, name="text",
            max_tokens=max_tokens, temperature=temperature, deliberate=False,
        )
        key = self.cache.key("parasail.text", body)
        cached = self.cache.get(key)
        if cached is not None:
            self.ledger.record(model, None, 0, cached=True)
            return cached["text"]
        if self.offline:
            raise OfflineMiss("parasail text call not in cache")
        t0 = time.time()
        resp = self._sync.chat.completions.create(**body)
        latency = int((time.time() - t0) * 1000)
        text = resp.choices[0].message.content or ""
        self.ledger.record(model, resp.usage, latency)
        self.cache.put(key, {"text": text, "latency_ms": latency}, "parasail.text")
        return text

    def embed_sync(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or settings.model_embed
        out: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for i, t in enumerate(texts):
            cached = self.cache.get(self.cache.key("parasail.embed", {"model": model, "text": t}))
            if cached is not None:
                out[i] = cached
            else:
                missing.append(i)
        if missing:
            if self.offline:
                raise OfflineMiss("embeddings not in cache")
            t0 = time.time()
            resp = self._sync.embeddings.create(model=model, input=[texts[i] for i in missing])
            self.ledger.record(model, resp.usage, int((time.time() - t0) * 1000))
            for j, item in enumerate(resp.data):
                idx = missing[j]
                out[idx] = item.embedding
                self.cache.put(
                    self.cache.key("parasail.embed", {"model": model, "text": texts[idx]}), item.embedding, "parasail.embed"
                )
        return [v for v in out if v is not None]

    # ------------------------------------------------------------------ async surface (flow)
    async def structured(self, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self.structured_sync, **kwargs)

    async def complete(self, **kwargs: Any) -> str:
        return await asyncio.to_thread(self.complete_sync, **kwargs)

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_sync, texts, model)

    async def stream_structured(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 2500,
        temperature: float = 0.2,
        deliberate: bool = False,
        replay_delay: float = 0.12,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a schema-locked completion.

        Yields {"type": "item", "item": {...}} as soon as each element of the top-level
        "components" array closes, {"type": "delta", "text": ...} for raw tokens, and finally
        {"type": "final", "json": {...}, "latency_ms": int, "replayed": bool}.
        """
        model = model or settings.model_fast
        body = self._chat_body(
            model=model, system=system, user=user, schema=schema, name=name,
            max_tokens=max_tokens, temperature=temperature, deliberate=deliberate,
        )
        key = self.cache.key("parasail.stream", body)
        cached = self.cache.get(key)
        if cached is not None:
            self.ledger.record(model, None, 0, cached=True)
            for item in cached["items"]:
                yield {"type": "item", "item": item}
                await asyncio.sleep(replay_delay)
            yield {"type": "final", "json": cached["json"], "latency_ms": cached["latency_ms"], "replayed": True}
            return
        if self.offline:
            raise OfflineMiss(f"parasail stream call not in cache: {name}")
        t0 = time.time()
        parser = ComponentStreamParser()
        stream = await self._async.chat.completions.create(stream=True, **body)
        usage = None
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content if chunk.choices[0].delta else None
            if not delta:
                continue
            for item in parser.feed(delta):
                yield {"type": "item", "item": item}
            yield {"type": "delta", "text": delta}
        latency = int((time.time() - t0) * 1000)
        final = parser.final()
        if final is None:
            try:
                final = _extract_json(parser.text())
            except Exception:
                final = {"components": parser.items}
        self.ledger.record(model, usage, latency)
        self.cache.put(
            key, {"items": parser.items, "json": final, "latency_ms": latency, "model": model}, "parasail.stream"
        )
        yield {"type": "final", "json": final, "latency_ms": latency, "replayed": False}


_client: ParasailClient | None = None


def get_parasail() -> ParasailClient:
    global _client
    if _client is None:
        _client = ParasailClient()
    return _client
