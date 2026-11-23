from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import current_account, issue

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)


@router.post("/register")
def register(body: Credentials, request: Request):
    st = request.app.state.app
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", body.username):
        raise HTTPException(400, "username may only contain letters, digits, _ . -")
    if st.account_by_name(body.username):
        raise HTTPException(409, "username taken")
    acc = st.create_account(body.username, body.password, body.display_name)
    return {"token": issue(acc), "account": acc}


@router.post("/login")
def login(body: Credentials, request: Request):
    acc = request.app.state.app.verify(body.username, body.password)
    if not acc:
        raise HTTPException(401, "wrong username or password")
    return {"token": issue(acc), "account": acc}


@router.get("/demo-accounts")
def demo_accounts(request: Request):
    return [
        {"username": d["username"], "display_name": d["display_name"], "password": d["password"]}
        for d in request.app.state.app.scenarios["demo_users"]
    ]


@router.get("/me")
def me(acc=Depends(current_account)):
    return acc
