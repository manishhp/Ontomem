"""Evaluation lab: browse precomputed results, launch new runs in the background, inspect failures."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..auth import current_account

router = APIRouter(prefix="/api/eval", tags=["evaluation"])
_REPO_RESULTS = Path(__file__).resolve().parents[4] / "results"  # editable install: <repo>/results
RESULTS = Path(os.environ.get("ONTOMEM_RESULTS") or (_REPO_RESULTS if _REPO_RESULTS.is_dir() else "results"))
SAFE = {"modeA", "modeB", "noise", "sweep", "evolution", "llmA", "llmB"}


def _runs_dir(request: Request) -> Path:
    p = request.app.state.app.home / "runs"
    p.mkdir(exist_ok=True)
    return p


@router.get("/results")
def results():
    """Experiments shipped with the repo (make reproduce)."""
    out = {}
    for name in sorted(SAFE):
        p = RESULTS / name
        if (p / "summary.json").exists():
            s = json.loads((p / "summary.json").read_text())
            out[name] = {"meta": s["meta"], "table": s["table"], "tests": s.get("tests_clean_retrieval_vs_S3", {}),
                         "error_stages": s.get("error_stages", {}), "by_case": s.get("by_case", {})}
        elif (p / "sweep.json").exists():
            out[name] = {"sweep": json.loads((p / "sweep.json").read_text())}
        elif (p / "evolution.json").exists():
            out[name] = json.loads((p / "evolution.json").read_text())
    return out


@router.get("/figures/{name}")
def figure(name: str):
    if not name.replace("_", "").replace(".", "").isalnum() or not name.endswith((".png", ".svg")):
        raise HTTPException(400, "bad name")
    p = RESULTS / "figures" / name
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p)


@router.get("/figures")
def figures():
    p = RESULTS / "figures"
    return sorted(x.name for x in p.glob("*.png")) if p.exists() else []


@router.get("/failures")
def failures(request: Request, experiment: str = "modeA", system: str | None = None, type: str | None = None,
             case: str | None = None, limit: int = Query(200, le=1000)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", experiment):
        raise HTTPException(400, "bad experiment name")
    base = RESULTS / experiment if experiment in SAFE else _runs_dir(request) / experiment
    p = base / "failures.jsonl"
    if not p.exists():
        raise HTTPException(404, "no failures file for that experiment")
    out = []
    for line in p.read_text().splitlines():
        x = json.loads(line)
        if system and x["system"] != system:
            continue
        if type and x["type"] != type:
            continue
        if case and x.get("case") != case:
            continue
        out.append(x)
        if len(out) >= limit:
            break
    return out


class RunIn(BaseModel):
    mode: str = Field("A", pattern="^[AB]$")
    split: str = Field("test", pattern="^(train|dev|test)$")
    systems: list[str] = ["S0", "S1", "S3", "S4"]
    limit: int | None = Field(40, ge=1, le=150)
    noise: float = Field(0.0, ge=0, le=0.5)


@router.post("/runs")
def start_run(body: RunIn, request: Request, acc=Depends(current_account)):
    allowed = {"S0", "S1", "S3", "S4", "S5", "ablations", "shacl", "S2", "S2b"}
    for s in body.systems:
        if s not in allowed and not s.startswith("sim:"):
            raise HTTPException(400, f"unknown system {s}")
    st = request.app.state.app
    rid = f"run-{uuid.uuid4().hex[:8]}"
    out = _runs_dir(request) / rid
    job = {"id": rid, "params": body.model_dump(), "status": "running", "progress": "starting",
           "created_at": datetime.now().isoformat(timespec="seconds"), "by": acc["username"]}
    st.jobs[rid] = job

    def work():
        from ...evaluation.runner import evaluate

        try:
            s = evaluate(body.mode, body.split, body.systems, out=out, noise=body.noise, limit=body.limit, bootstrap=200,
                         progress=lambda i, n, name: job.update(progress=f"{i + 1}/{n}: {name}"))
            job.update(status="done", progress="done", summary={"meta": s["meta"], "table": s["table"],
                                                                 "tests": s.get("tests_clean_retrieval_vs_S3", {})})
        except Exception as e:  # pragma: no cover
            job.update(status="error", error=str(e))

    threading.Thread(target=work, daemon=True).start()
    return job


@router.get("/runs")
def list_runs(request: Request):
    return sorted(request.app.state.app.jobs.values(), key=lambda j: j["created_at"], reverse=True)


@router.get("/runs/{rid}")
def get_run(rid: str, request: Request):
    j = request.app.state.app.jobs.get(rid)
    if not j:
        raise HTTPException(404, "unknown run")
    return j
