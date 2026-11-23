"""Chat, memory, explanations, clarifications, scenarios, compare mode, SPARQL, validation and privacy."""

from __future__ import annotations

import asyncio
import functools
import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ...core.extract.rules import QUESTION, normalise
from ...core.rdf import SparqlStore, memory_graph
from ...core.validation import ShaclValidator, validate_graph
from ..auth import current_account
from ..views import compare, graph_view, memory_view, node_detail, timeline_view

router = APIRouter(prefix="/api", tags=["memory"])


def locked(fn):
    """Run a handler while holding the app lock (projections are plain dicts, writes mutate them)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with kwargs["request"].app.state.app.lock:
            return fn(*args, **kwargs)

    return wrapper
REQUEST_WORDS = re.compile(r"\b(?:suggest|recommend|ideas?|what should|where should|any .* ideas|make me|give me|help me pick|plan a)\b")


def _app(request: Request):
    return request.app.state.app


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"bad timestamp {s!r}") from None


def is_request(text: str) -> bool:
    t = normalise(text)
    return t.endswith("?") or bool(QUESTION.match(t)) or bool(REQUEST_WORDS.search(t))


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    ts: str | None = None
    context: str | None = None


def _chat(st, acc, body: ChatIn) -> dict:
    ts = _ts(body.ts) or datetime.now().replace(microsecond=0)
    with st.lock:
        trace = st.engine.observe(acc["id"], body.text, ts)
        st.collect_proposals()
        out = {"trace": trace.to_dict(), "answer": None}
        wrote = any(c.accepted for c in trace.candidates)
        if is_request(body.text) or not wrote:
            if is_request(body.text):
                out["answer"] = st.engine.ask(acc["id"], body.text, context=body.context, now=ts)
        if out["answer"] is None:
            n = sum(1 for c in trace.candidates if c.accepted)
            parts = []
            for c in trace.candidates:
                if c.accepted and c.outcome:
                    rules = [f["rule"] for f in c.outcome["firings"]]
                    parts.append(f"{c.raw.get('polarity')} {c.mapping.get('label')} ({', '.join(rules)})")
            out["reply"] = (f"Noted {n} preference{'s' if n != 1 else ''}: " + "; ".join(parts) + ".") if n else \
                "Got it. (I didn't find a preference in that - try telling me what you like or avoid.)"
            if trace.clarifications:
                out["reply"] += " " + trace.clarifications[0]["question"]
        else:
            out["reply"] = out["answer"]["reply"]
        return out


@router.post("/chat")
def chat(body: ChatIn, request: Request, acc=Depends(current_account)):
    return _chat(_app(request), acc, body)


@router.get("/chat/stream")
async def chat_stream(request: Request, text: str, ts: str | None = None, acc=Depends(current_account)):
    """Server-sent events: one event per pipeline stage, so the UI can animate the inspector."""
    st = _app(request)
    result = _chat(st, acc, ChatIn(text=text, ts=ts))

    async def gen():
        tr = result["trace"]
        yield {"event": "extract", "data": json.dumps({"candidates": [c["raw"] for c in tr["candidates"]], "ms": tr["timings_ms"].get("extract")})}
        for i, c in enumerate(tr["candidates"]):
            for stage in ("mapping", "validation", "key", "outcome"):
                await asyncio.sleep(0.12)
                yield {"event": stage, "data": json.dumps({"index": i, stage: c.get(stage), "repairs": c.get("repairs")})}
        await asyncio.sleep(0.12)
        yield {"event": "done", "data": json.dumps(result, default=str)}

    return EventSourceResponse(gen())


@router.get("/memory")
@locked
def memory(request: Request, as_of: str | None = None, acc=Depends(current_account)):
    return memory_view(_app(request).engine, acc["id"], _ts(as_of))


@router.get("/memory/graph")
@locked
def graph(request: Request, as_of: str | None = None, history: bool = True, ancestors: bool = False,
          acc=Depends(current_account)):
    return graph_view(_app(request).engine, acc["id"], _ts(as_of), history, ancestors)


@router.get("/memory/timeline")
@locked
def timeline(request: Request, acc=Depends(current_account)):
    return timeline_view(_app(request).engine, acc["id"])


@router.get("/memory/node/{node_id}")
@locked
def node(node_id: str, request: Request, acc=Depends(current_account)):
    try:
        return node_detail(_app(request).engine, acc["id"], node_id)
    except KeyError:
        raise HTTPException(404, "no such preference") from None


@router.get("/memory/events")
@locked
def events(request: Request, acc=Depends(current_account)):
    return [e.to_dict() for e in _app(request).store.events(acc["id"])]


class AskIn(BaseModel):
    text: str
    context: str | None = None
    who: str | None = None
    budget_tokens: int = Field(default=160, ge=20, le=2000)


@router.post("/ask")
@locked
def ask(body: AskIn, request: Request, acc=Depends(current_account)):
    return _app(request).engine.ask(acc["id"], body.text, body.context, body.who, budget_tokens=body.budget_tokens)


@router.get("/clarifications")
@locked
def clarifications(request: Request, acc=Depends(current_account)):
    return _app(request).engine.clarifications(acc["id"])


class ClarifyIn(BaseModel):
    keep_exception: bool


@router.post("/clarifications/{node_id}")
def clarify(node_id: str, body: ClarifyIn, request: Request, acc=Depends(current_account)):
    st = _app(request)
    with st.lock:
        if node_id not in st.engine.memory(acc["id"]).nodes:
            raise HTTPException(404, "no such preference")
        out = st.engine.resolve_clarification(acc["id"], node_id, body.keep_exception)
    return {"outcome": out.to_dict(), "clarifications": st.engine.clarifications(acc["id"])}


# ----------------------------------------------------------------- scenarios
@router.get("/scenarios")
def scenarios(request: Request):
    return _app(request).scenarios["scenarios"]


@router.post("/scenarios/{sid}/play")
def play(sid: str, request: Request, acc=Depends(current_account)):
    """Play a scripted conversation into a fresh sandbox memory and return every step's trace."""
    st = _app(request)
    sc = next((s for s in st.scenarios["scenarios"] if s["id"] == sid), None)
    if sc is None:
        raise HTTPException(404, "unknown scenario")
    from ...core.engine import OntoMemEngine
    from ...core.events import EventStore

    eng = OntoMemEngine(ontology=st.ontology, store=EventStore())
    steps = []
    for t in sc["turns"]:
        ts = datetime.fromisoformat(t["ts"])
        tr = eng.observe("sandbox", t["text"], ts)
        step = {"text": t["text"], "ts": t["ts"], "trace": tr.to_dict()}
        if is_request(t["text"]):
            step["answer"] = eng.ask("sandbox", t["text"], now=ts)
        steps.append(step)
    return {"scenario": sc, "steps": steps, "memory": memory_view(eng, "sandbox"), "graph": graph_view(eng, "sandbox")}


class CompareIn(BaseModel):
    turns: list[dict[str, str]]
    baseline: str = "S1"
    query: str | None = None


@router.post("/compare")
def compare_route(body: CompareIn, request: Request, acc=Depends(current_account)):
    if not body.turns or len(body.turns) > 60:
        raise HTTPException(400, "give 1-60 turns")
    return compare(_app(request).engine, body.turns, body.baseline, body.query)


# ------------------------------------------------------------ RDF / SHACL
class SparqlIn(BaseModel):
    query: str = Field(max_length=10_000)


@router.post("/sparql")
@locked
def sparql(body: SparqlIn, request: Request, acc=Depends(current_account)):
    st = _app(request)
    mem = st.engine.memory(acc["id"])
    g = memory_graph(acc["id"], mem, st.engine.reasoner, st.ontology)
    try:
        res = SparqlStore(g).query(body.query)
    except Exception as e:
        raise HTTPException(400, f"SPARQL error: {e}") from None
    return {**res, "triples_in_graph": len(g)}


@router.post("/validate")
def validate(candidate: dict, request: Request, acc=Depends(current_account)):
    """Validate one candidate preference with real SHACL (pyshacl)."""
    return ShaclValidator(_app(request).ontology).validate(candidate).to_dict()


@router.get("/validate/graph")
@locked
def validate_memory(request: Request, acc=Depends(current_account)):
    """Whole-memory SHACL check, including the graph-level invariants I1 and I2."""
    st = _app(request)
    mem = st.engine.memory(acc["id"])
    g = memory_graph(acc["id"], mem, st.engine.reasoner, st.ontology)
    rep = validate_graph(g)
    return {**rep.to_dict(), "triples": len(g)}


# ----------------------------------------------------------------- privacy
@router.get("/me/export")
@locked
def export(request: Request, fmt: str = "json-ld", acc=Depends(current_account)):
    st = _app(request)
    mem = st.engine.memory(acc["id"])
    g = memory_graph(acc["id"], mem, st.engine.reasoner, st.ontology)
    if fmt == "turtle":
        return PlainTextResponse(g.serialize(format="turtle"), media_type="text/turtle")
    return PlainTextResponse(g.serialize(format="json-ld", indent=1), media_type="application/ld+json")


@router.delete("/me/data")
def erase(request: Request, acc=Depends(current_account)):
    st = _app(request)
    with st.lock:
        n = st.engine.erase(acc["id"])
    return {"erased_events": n, "tombstone": True}


@router.post("/me/reset-demo")
def reset_demo(request: Request, acc=Depends(current_account)):
    try:
        _app(request).reset_demo(acc["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True}
