"""You.com: the only source of facts from outside the building.

Three surfaces are used:
  * Web Search with highlight extraction (ydc-index.io/v1/search) for fast, passage-level grounding;
  * Contents (ydc-index.io/v1/contents) to read a page the swarm already knows it needs;
  * Research (api.you.com/v1/research) with an output schema, which returns cited JSON in one call.

Every result that reaches the UI carries a source id that maps back to a real URL from one of
these calls, so the station can show a citation for every recommendation it makes.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..cache import OfflineMiss, get_cache
from ..config import settings

SEARCH_URL = "https://ydc-index.io/v1/search"
CONTENTS_URL = "https://ydc-index.io/v1/contents"
RESEARCH_URL = "https://api.you.com/v1/research"


class YouClient:
    def __init__(self, api_key: str | None = None, offline: bool | None = None):
        self.api_key = api_key or settings.ydc_api_key
        self.offline = settings.offline if offline is None else offline
        self.cache = get_cache()
        self.calls = 0
        self.cached = 0
        self.latency_ms: list[int] = []
        self._http = httpx.Client(timeout=httpx.Timeout(60.0, read=120.0))

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def _post(self, url: str, body: dict[str, Any], namespace: str) -> Any:
        key = self.cache.key(namespace, {"url": url, "body": body})
        cached = self.cache.get(key)
        if cached is not None:
            self.cached += 1
            return cached
        if self.offline:
            raise OfflineMiss(f"you.com call not in cache: {namespace}")
        if not self.api_key:
            raise RuntimeError("YDC_API_KEY is not set")
        t0 = time.time()
        for attempt in range(3):
            resp = self._http.post(url, headers=self._headers(), json=body)
            if resp.status_code == 429 and attempt < 2:
                wait = float(resp.headers.get("Retry-After", "1") or 1)
                time.sleep(min(wait, 5.0))
                continue
            break
        resp.raise_for_status()
        self.calls += 1
        self.latency_ms.append(int((time.time() - t0) * 1000))
        data = resp.json()
        self.cache.put(key, data, namespace)
        return data

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str,
        *,
        count: int = 5,
        freshness: str | None = None,
        include_domains: list[str] | None = None,
        highlights: bool = True,
        max_tokens: int = 1536,
        country: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return normalised sources: {title, url, snippet, page_age, kind}."""
        body: dict[str, Any] = {"query": query, "count": max(1, min(count, 20))}
        if freshness:
            body["freshness"] = freshness
        if include_domains:
            body["include_domains"] = include_domains
        if country:
            body["country"] = country
        if highlights:
            body["extraction"] = {
                "extraction_mode": "highlights",
                "highlights": {"total_max_tokens": max(1024, min(max_tokens, 8192))},
            }
        data = self._post(SEARCH_URL, body, "youcom.search")
        out: list[dict[str, Any]] = []
        results = data.get("results", {})
        for r in results.get("web", []) or []:
            contents = r.get("contents") or {}
            hl = contents.get("highlights") or []
            snippet = " ".join(h.strip() for h in hl[:3]) if hl else " ".join(r.get("snippets") or [])[:600]
            if not snippet:
                snippet = r.get("description") or ""
            out.append(
                {
                    "title": r.get("title") or r.get("url"),
                    "url": r.get("url"),
                    "snippet": snippet[:900],
                    "page_age": r.get("page_age"),
                    "kind": "web",
                }
            )
        for r in results.get("news", []) or []:
            out.append(
                {
                    "title": r.get("title") or r.get("url"),
                    "url": r.get("url"),
                    "snippet": (r.get("description") or "")[:600],
                    "page_age": r.get("page_age"),
                    "kind": "web",
                }
            )
        return out

    # ------------------------------------------------------------------ contents
    def contents(self, urls: list[str], *, formats: list[str] | None = None, max_chars: int = 6000) -> list[dict[str, Any]]:
        body = {"urls": urls[:10], "formats": formats or ["markdown", "metadata"]}
        data = self._post(CONTENTS_URL, body, "youcom.contents")
        items = data if isinstance(data, list) else data.get("results", [])
        out = []
        for item in items:
            md = (item.get("markdown") or "")[:max_chars]
            out.append({"url": item.get("url"), "title": item.get("title") or item.get("url"), "markdown": md, "kind": "page"})
        return out

    # ------------------------------------------------------------------ research
    def research(
        self,
        prompt: str,
        *,
        effort: str = "lite",
        output_schema: dict[str, Any] | None = None,
        include_domains: list[str] | None = None,
        freshness: str | None = None,
    ) -> dict[str, Any]:
        """Run the Research API. Returns {content, content_type, sources:[{title,url,snippet}]}."""
        body: dict[str, Any] = {"input": prompt, "research_effort": effort}
        if output_schema is not None:
            body["output_schema"] = output_schema
        control: dict[str, Any] = {}
        if include_domains:
            control["include_domains"] = include_domains
        if freshness:
            control["freshness"] = freshness
        if control:
            body["source_control"] = control
        data = self._post(RESEARCH_URL, body, "youcom.research")
        output = data.get("output", data)
        sources = []
        for i, s in enumerate(output.get("sources", []) or []):
            snippets = s.get("snippets") or []
            sources.append(
                {
                    "title": s.get("title") or s.get("url"),
                    "url": s.get("url"),
                    "snippet": (" ".join(snippets)[:600] if snippets else ""),
                    "kind": "research",
                    "citation_index": i + 1,
                }
            )
        return {"content": output.get("content"), "content_type": output.get("content_type"), "sources": sources}

    # ------------------------------------------------------------------ async wrappers
    async def asearch(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.search, *args, **kwargs)

    async def acontents(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.contents, *args, **kwargs)

    async def aresearch(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self.research, *args, **kwargs)

    def summary(self) -> dict[str, Any]:
        lat = sorted(self.latency_ms)
        return {
            "calls": self.calls,
            "cached": self.cached,
            "p50_latency_ms": lat[len(lat) // 2] if lat else None,
        }


_client: YouClient | None = None


def get_you() -> YouClient:
    global _client
    if _client is None:
        _client = YouClient()
    return _client
