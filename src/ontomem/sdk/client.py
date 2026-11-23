"""Python client for the OntoMem API.

    from ontomem.sdk.client import OntoMemClient
    c = OntoMemClient("http://localhost:8000")
    c.login("asha", "demo1234")
    c.say("I love spicy food.")
    c.ask("Any dinner ideas?")["recommendations"]

``OntoMemClient.local()`` gives the same interface without a server (in-process engine).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class OntoMemClient:
    def __init__(self, base_url: str = "http://localhost:8000", token: str | None = None, timeout: float = 30) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.http = httpx.Client(timeout=timeout)

    # -------------------------------------------------------------- session
    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _req(self, method: str, path: str, **kw) -> Any:
        r = self.http.request(method, self.base + path, headers=self._h(), **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        ct = r.headers.get("content-type", "")
        return r.json() if "json" in ct else r.text

    def register(self, username: str, password: str, display_name: str | None = None) -> dict:
        d = self._req("POST", "/api/auth/register", json={"username": username, "password": password, "display_name": display_name})
        self.token = d["token"]
        return d["account"]

    def login(self, username: str, password: str) -> dict:
        d = self._req("POST", "/api/auth/login", json={"username": username, "password": password})
        self.token = d["token"]
        return d["account"]

    # --------------------------------------------------------------- memory
    def say(self, text: str, ts: datetime | None = None) -> dict:
        return self._req("POST", "/api/chat", json={"text": text, "ts": ts.isoformat() if ts else None})

    def ask(self, text: str, context: str | None = None, who: str | None = None, budget_tokens: int = 160) -> dict:
        return self._req("POST", "/api/ask", json={"text": text, "context": context, "who": who, "budget_tokens": budget_tokens})

    def memory(self, as_of: datetime | None = None) -> dict:
        return self._req("GET", "/api/memory", params={"as_of": as_of.isoformat()} if as_of else None)

    def timeline(self) -> list:
        return self._req("GET", "/api/memory/timeline")

    def clarifications(self) -> list:
        return self._req("GET", "/api/clarifications")

    def answer(self, node_id: str, keep_exception: bool) -> dict:
        return self._req("POST", f"/api/clarifications/{node_id}", json={"keep_exception": keep_exception})

    def sparql(self, query: str) -> dict:
        return self._req("POST", "/api/sparql", json={"query": query})

    def export(self, fmt: str = "json-ld") -> str:
        return self._req("GET", "/api/me/export", params={"fmt": fmt})

    def forget_me(self) -> dict:
        return self._req("DELETE", "/api/me/data")

    @staticmethod
    def local(home: str | None = None) -> LocalClient:
        return LocalClient(home)


class LocalClient:
    """Same verbs, in-process (no server, no auth). Useful in notebooks and scripts."""

    def __init__(self, home: str | None = None, user: str = "local") -> None:
        from pathlib import Path

        from ..core.engine import OntoMemEngine
        from ..core.events import EventStore

        store = EventStore(Path(home) / "events.sqlite") if home else EventStore()
        self.engine = OntoMemEngine(store=store)
        self.user = user

    def say(self, text: str, ts: datetime | None = None) -> dict:
        return self.engine.observe(self.user, text, ts).to_dict()

    def ask(self, text: str, context: str | None = None, who: str | None = None, budget_tokens: int = 160) -> dict:
        return self.engine.ask(self.user, text, context, who, budget_tokens=budget_tokens)

    def memory(self, as_of: datetime | None = None) -> list[dict]:
        return [n.to_dict() for n in self.engine.memory(self.user, as_of).active()]

    def clarifications(self) -> list:
        return self.engine.clarifications(self.user)
