from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import current_account

router = APIRouter(prefix="/api/ontology", tags=["ontology"])


def _st(request: Request):
    return request.app.state.app


@router.get("")
def summary(request: Request):
    o = _st(request).ontology
    return {
        "version": o.version,
        "concepts": len(o),
        "domains": [{"id": r, "label": o.label(r), "concepts": len(o.in_domain(r))} for r in o.roots],
        "max_depth": max(o.depth(c) for c in o.concepts),
        "labels": len(o.lexicon),
    }


@router.get("/tree")
def tree(request: Request, root: str | None = None):
    o = _st(request).ontology
    if root and root not in o:
        raise HTTPException(404, "unknown concept")
    return o.tree(root)


@router.get("/search")
def search(q: str, request: Request, limit: int = 20):
    o = _st(request).ontology
    return [{**c.to_dict(), "path": o.path(c.id)} for c in o.search(q, limit)]


@router.get("/concept/{cid}")
def concept(cid: str, request: Request):
    o = _st(request).ontology
    if cid not in o:
        raise HTTPException(404, "unknown concept")
    return {**o.concepts[cid].to_dict(), "path": o.path(cid), "depth": o.depth(cid),
            "descendants": len(o.descendants(cid)), "related_expansion": sorted(o.expand(cid))[:40]}


@router.get("/map")
def map_phrase(q: str, request: Request):
    st = _st(request)
    m = st.engine.mapper.map(q)
    need, why = st.evolver.needs_review(q)
    return {"phrase": q, "concept": m.concept, "label": st.ontology.label(m.concept), "method": m.method,
            "score": round(m.score, 3), "matched": m.matched, "path": st.ontology.path(m.concept) if m.mapped else [],
            "needs_review": need, "why": why}


@router.get("/proposals")
def proposals(request: Request, status: str | None = "pending"):
    return _st(request).proposals(status)


@router.post("/proposals/refresh")
def refresh(request: Request, acc=Depends(current_account)):
    return {"added": _st(request).collect_proposals()}


class Decision(BaseModel):
    approve: bool
    choice: int | None = 0
    parent: str | None = None  # manual placement
    label: str | None = None


@router.post("/proposals/{pid}")
def decide(pid: str, body: Decision, request: Request, acc=Depends(current_account)):
    st = _st(request)
    if body.parent and body.parent not in st.ontology:
        raise HTTPException(400, "unknown parent concept")
    try:
        return st.decide(pid, body.choice, body.approve, acc["username"], body.parent, body.label)
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e)) from None


@router.get("/changelog")
def changelog(request: Request):
    return _st(request).changelog()


@router.get("/turtle")
def turtle(request: Request):
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(_st(request).ontology.to_graph().serialize(format="turtle"), media_type="text/turtle")
