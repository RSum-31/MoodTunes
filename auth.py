import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from passlib.context import CryptContext

from lib.db import db

SESSION_COOKIE = "mt_session"
SESSION_DAYS = 30
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_pwd.hash, password)  # bcrypt is CPU-bound; keep the loop free


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(_pwd.verify, password, password_hash)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def start_session(response: Response, user_id: str) -> None:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    await db.sessions.insert_one({"token_hash": _digest(token), "user_id": user_id, "expires_at": expires_at})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=SESSION_DAYS * 86400,
        path="/",
    )


async def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.sessions.delete_one({"token_hash": _digest(token)})
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


async def get_optional_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = await db.sessions.find_one({"token_hash": _digest(token)})
    if not session:
        return None
    if session["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await db.sessions.delete_one({"_id": session["_id"]})
        return None
    return await db.users.find_one({"id": session["user_id"]}, {"_id": 0, "password_hash": 0})


async def get_current_user(user: dict | None = Depends(get_optional_user)) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user
