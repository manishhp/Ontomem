"""OntoMem as an MCP server, so any MCP-capable agent can use it as long-term preference memory.

Tools:  remember(text)  ->  what was stored and which rule fired
        recall(request) ->  the packed memory block + suggestions, with citations
        explain(item)   ->  proof tree for why something is / isn't suggested
        clarify()       ->  open questions the memory wants answered (rule R3)
        forget()        ->  erase everything (right to be forgotten)

Run (stdio, for desktop agents):   python -m ontomem.mcp_server.server
Configure in an MCP client, e.g.:
    {"command": "python", "args": ["-m", "ontomem.mcp_server.server"], "env": {"ONTOMEM_HOME": "~/.ontomem"}}

Supports the ``mcp`` SDK v2 (MCPServer) and v1 (FastMCP).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.engine import OntoMemEngine
from ..core.events import EventStore

USER = os.environ.get("ONTOMEM_MCP_USER", "mcp-user")


def build_engine() -> OntoMemEngine:
    home = Path(os.path.expanduser(os.environ.get("ONTOMEM_HOME", "~/.ontomem")))
    home.mkdir(parents=True, exist_ok=True)
    return OntoMemEngine(store=EventStore(home / "events.sqlite"))


def tools(engine: OntoMemEngine) -> dict[str, Any]:
    """Plain functions (also used by tests and the SDK's local mode)."""

    def remember(text: str) -> dict:
        """Store any preferences stated in the user's message. Returns what changed and why."""
        tr = engine.observe(USER, text, datetime.now().replace(microsecond=0))
        return {
            "stored": [
                {"preference": f"{c.raw.get('who')}: {c.raw.get('polarity')} {c.mapping.get('label')}",
                 "slot": c.key, "rules": [f["rule"] for f in (c.outcome or {}).get("firings", [])]}
                for c in tr.candidates if c.accepted
            ],
            "clarifications": [q["question"] for q in tr.clarifications],
        }

    def recall(request: str, budget_tokens: int = 160) -> dict:
        """Get the user's relevant, current preferences (and suggestions) for a request."""
        a = engine.ask(USER, request, budget_tokens=budget_tokens)
        return {"memory_block": a["memory_block"]["block"],
                "suggestions": [{"name": r["item"]["name"], "why": r["reasons"]} for r in a["recommendations"]],
                "excluded": a["excluded_count"], "open_questions": [q["question"] for q in a["clarifications"]]}

    def explain(request: str) -> dict:
        """Explain the top suggestion for a request: which statements decided it, which were overruled."""
        a = engine.ask(USER, request)
        if not a["recommendations"]:
            return {"explanation": "nothing to explain yet"}
        r = a["recommendations"][0]
        return {"item": r["item"]["name"], "proof": r["proof"], "counterfactual": (r["counterfactual"] or {}).get("summary")}

    def clarify() -> list[dict]:
        """Questions the memory needs answered before relying on a preference (rule R3)."""
        return engine.clarifications(USER)

    def forget() -> dict:
        """Erase all of this user's memory."""
        return {"erased_events": engine.erase(USER)}

    return {"remember": remember, "recall": recall, "explain": explain, "clarify": clarify, "forget": forget}


def create_server(engine: OntoMemEngine | None = None):
    """An MCP server exposing the five tools (works with mcp SDK v2 and v1)."""
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
    server = Server("ontomem", instructions="Long-term preference memory with ontology-guided conflict resolution.")
    for fn in tools(engine or build_engine()).values():
        server.tool()(fn)
    return server


def main() -> None:  # pragma: no cover - needs an MCP client
    create_server().run("stdio")


if __name__ == "__main__":
    main()
