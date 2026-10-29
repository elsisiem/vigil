"""One (withone.ai): the station's hands outside the incident channel.

One gives the swarm managed, already-authorised access to the team's tools. Vigil uses it for
the after-action record: when a run completes, a post-incident review issue is opened in the
team's GitHub repository through One's GitHub connection, with the decision, the runbook and
the cited sources. The CLI is driven with `--agent` so every response is JSON.

Requirements: Node 22.23.4+ (or 24.22+/26.12+) because the One CLI refuses older Node, and a
`one init --auth manual --api-key $ONE_SECRET` on the machine, or ONE_SECRET in the environment.
Dry-run mode uses the CLI's own `--dry-run` so the request is validated without being sent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

from ..config import settings
from ..models import ActionResult

CLI_PACKAGE = "@withone/cli@2.10.0"
GITHUB_CREATE_ISSUE_INTENT = "create an issue"


class OneClient:
    def __init__(self, node_bin_dir: str | None = None, secret: str | None = None):
        self.node_bin_dir = node_bin_dir or os.environ.get("VIGIL_NODE_BIN", "")
        self.secret = secret or settings.one_secret
        self._env = dict(os.environ)
        if self.node_bin_dir:
            self._env["PATH"] = self.node_bin_dir + os.pathsep + self._env.get("PATH", "")
        if self.secret:
            self._env["ONE_SECRET"] = self.secret
        self._env["ONE_AGENT"] = "1"
        self._connection_cache: dict[str, str] = {}
        self._action_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ plumbing
    def _node_ok(self) -> tuple[bool, str]:
        node = shutil.which("node", path=self._env.get("PATH"))
        if not node:
            return False, "node not found"
        try:
            v = subprocess.run(
                [node, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env, timeout=20
            ).stdout.strip()
        except Exception as e:  # pragma: no cover
            return False, str(e)
        major = int(v.lstrip("v").split(".")[0]) if v.startswith("v") else 0
        return (major >= 22 and major not in (23, 25)), v

    def available(self) -> tuple[bool, str]:
        if not self.secret:
            return False, "ONE_SECRET not set"
        ok, v = self._node_ok()
        if not ok:
            return False, f"One CLI needs Node 22.23.4+; found {v}"
        return True, v

    def _run(self, args: list[str], timeout: int = 120) -> Any:
        one = None
        if os.name == "nt":
            one = shutil.which("one.cmd", path=self._env.get("PATH")) or shutil.which("one.exe", path=self._env.get("PATH"))
        else:
            one = shutil.which("one", path=self._env.get("PATH"))
        cmd = [one, "--agent", *args] if one else [
            shutil.which("npx", path=self._env.get("PATH")) or "npx", "-y", CLI_PACKAGE, "--agent", *args
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env, timeout=timeout, shell=False
        )
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            start = out.find("{")
            if start >= 0:
                try:
                    return json.loads(out[start:])
                except json.JSONDecodeError:
                    pass
            return {"error": out[:800], "exit": proc.returncode}

    # ------------------------------------------------------------------ discovery
    def connections(self) -> list[dict[str, Any]]:
        data = self._run(["list"])
        return data.get("connections", []) if isinstance(data, dict) else []

    def connection_key(self, platform: str) -> str | None:
        if platform in self._connection_cache:
            return self._connection_cache[platform]
        for c in self.connections():
            if c.get("platform") == platform and c.get("state") == "operational":
                self._connection_cache[platform] = c["key"]
                return c["key"]
        return None

    def find_action(self, platform: str, intent: str) -> str | None:
        cache_key = f"{platform}:{intent}"
        if cache_key in self._action_cache:
            return self._action_cache[cache_key]
        data = self._run(["actions", "find", platform, intent])
        try:
            action_id = data["answers"][0]["selected"][0]["actionId"]
        except (KeyError, IndexError, TypeError):
            return None
        self._action_cache[cache_key] = action_id
        return action_id

    # ------------------------------------------------------------------ actions
    def execute(
        self,
        platform: str,
        action_id: str,
        connection_key: str,
        body: dict[str, Any] | None = None,
        path_vars: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> Any:
        args = ["actions", "execute", platform, action_id, connection_key]
        if body:
            args += ["-d", json.dumps(body)]
        if path_vars:
            args += ["--path-vars", json.dumps(path_vars)]
        if dry_run:
            args.append("--dry-run")
        return self._run(args)

    def create_github_issue(
        self, *, title: str, body: str, labels: list[str] | None = None, repo: str | None = None, dry_run: bool | None = None
    ) -> ActionResult:
        dry = settings.dry_run if dry_run is None else dry_run
        repo = repo or os.environ.get("GITHUB_REPO", "")
        t0 = time.time()
        ok, why = self.available()
        if not ok:
            return ActionResult(kind="one_action", ok=False, dry_run=dry, detail={"error": why, "title": title})
        if "/" not in repo:
            return ActionResult(kind="one_action", ok=False, dry_run=dry, detail={"error": "GITHUB_REPO must be owner/repo"})
        owner, name = repo.split("/", 1)
        key = self.connection_key("github")
        if not key:
            return ActionResult(kind="one_action", ok=False, dry_run=dry, detail={"error": "no operational GitHub connection in One; run `one add github`"})
        action_id = self.find_action("github", GITHUB_CREATE_ISSUE_INTENT)
        if not action_id:
            return ActionResult(kind="one_action", ok=False, dry_run=dry, detail={"error": "could not resolve GitHub create-issue action"})
        payload: dict[str, Any] = {"title": title[:250], "body": body[:60000]}
        if labels:
            payload["labels"] = labels
        result = self.execute(
            "github", action_id, key, body=payload, path_vars={"owner": owner, "repo": name}, dry_run=dry
        )
        detail: dict[str, Any] = {"repo": repo, "title": title, "latency_ms": int((time.time() - t0) * 1000)}
        if isinstance(result, dict):
            data = result.get("data", result.get("result", result))
            if isinstance(data, dict):
                if data.get("html_url"):
                    detail["url"] = data["html_url"]
                if data.get("number"):
                    detail["number"] = data["number"]
            if result.get("error"):
                detail["error"] = str(result["error"])[:500]
            if dry:
                detail["dry_run_response"] = json.dumps(result)[:500]
        ok = "error" not in detail
        return ActionResult(kind="one_action", ok=ok, dry_run=dry, detail=detail)


_client: OneClient | None = None


def get_one() -> OneClient:
    global _client
    if _client is None:
        _client = OneClient()
    return _client
