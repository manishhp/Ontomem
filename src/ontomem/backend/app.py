"""FastAPI application.

    uvicorn ontomem.backend.app:app --reload --port 8000      # dev
    open http://localhost:8000/docs                            # OpenAPI docs

If the built frontend exists (frontend/dist) it is served at / as well.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .routers import auth, evaluation, memory, ontology
from .state import AppState


def create_app(home: str | None = None, seed_demo: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not hasattr(app.state, "app"):
            app.state.app = AppState(home, seed_demo=seed_demo)
        yield

    app = FastAPI(
        title="OntoMem API",
        version=__version__,
        description="Ontology-guided preference memory for AI agents: write path, read path, explanations, "
                    "ontology evolution and the PrefRev evaluation lab.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("ONTOMEM_CORS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def timing(request: Request, call_next):
        t0 = time.perf_counter()
        resp = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        st = request.app.state.app
        st.requests += 1
        st.request_ms += ms
        resp.headers["X-Response-Time-ms"] = f"{ms:.1f}"
        return resp

    for r in (auth.router, memory.router, ontology.router, evaluation.router):
        app.include_router(r)

    @app.get("/api/health", tags=["system"])
    def health(request: Request):
        st = request.app.state.app
        return {"status": "ok", "version": __version__, "ontology_version": st.ontology.version,
                "uptime_s": round(time.time() - st.started), "llm": st.llm.name if st.llm else None,
                "sparql_engine": _sparql_engine()}

    @app.get("/api/stats", tags=["system"])
    def stats(request: Request):
        st = request.app.state.app
        users = st.engine.users()
        return {"accounts": len(st.accounts()), "users_with_memory": len(users), "events": st.store.count(),
                "concepts": len(st.ontology), "pending_proposals": len(st.proposals("pending")),
                "requests": st.requests, "mean_request_ms": round(st.request_ms / max(1, st.requests), 2)}

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        st = request.app.state.app
        lines = [
            "# HELP ontomem_requests_total HTTP requests served", "# TYPE ontomem_requests_total counter",
            f"ontomem_requests_total {st.requests}",
            "# HELP ontomem_request_ms_sum Total request time (ms)", "# TYPE ontomem_request_ms_sum counter",
            f"ontomem_request_ms_sum {st.request_ms:.1f}",
            "# HELP ontomem_events_total Events in the log", "# TYPE ontomem_events_total gauge",
            f"ontomem_events_total {st.store.count()}",
            "# HELP ontomem_concepts Concepts in the ontology", "# TYPE ontomem_concepts gauge",
            f"ontomem_concepts {len(st.ontology)}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n")

    dist = Path(os.environ.get("ONTOMEM_FRONTEND", Path(__file__).resolve().parents[3] / "frontend" / "dist"))
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        root = dist.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "Not Found")  # a mistyped API URL must not return the web page
            f = (root / path).resolve()
            # resolve() collapses "../" (also when sent as %2f-encoded), so this refuses any file outside the app
            if path and f.is_relative_to(root) and f.is_file():
                return FileResponse(f)
            return FileResponse(root / "index.html")

    return app


def _sparql_engine() -> str:
    try:
        import pyoxigraph  # noqa: F401

        return "oxigraph"
    except ImportError:  # pragma: no cover
        return "rdflib"


app = create_app()
