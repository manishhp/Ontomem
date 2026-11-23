"""JWT authentication. Every memory route is scoped to the caller's own account (tenant isolation)."""

from __future__ import annotations

import os
import time
import warnings
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET = os.environ.get("ONTOMEM_SECRET", "")
if not SECRET:
    SECRET = "dev-only-secret-change-me-in-production-0000"
    warnings.warn("ONTOMEM_SECRET not set - using a development secret. Set it in production.", stacklevel=1)
ALGO = "HS256"
TTL = int(os.environ.get("ONTOMEM_TOKEN_TTL", 7 * 24 * 3600))

bearer = HTTPBearer(auto_error=False)


def issue(account: dict[str, Any]) -> str:
    now = int(time.time())
    return jwt.encode({"sub": account["id"], "name": account["username"], "iat": now, "exp": now + TTL}, SECRET, ALGO)


def current_account(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any]:
    token = cred.credentials if cred else request.query_params.get("token")  # EventSource can't set headers
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(token, SECRET, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired") from None
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None
    acc = request.app.state.app.account(claims["sub"])
    if acc is None:
        raise HTTPException(401, "account no longer exists")
    return acc
