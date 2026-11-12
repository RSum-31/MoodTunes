import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo.errors import DuplicateKeyError

from lib.auth import end_session, get_current_user, get_optional_user, hash_password, start_session, verify_password
from lib.db import db
from models.user import AuthMeResponse, LoginRequest, SettingsUpdate, SignupRequest, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


def _public(user: dict) -> UserPublic:
    return UserPublic(id=user["id"], email=user["email"], name=user["name"], save_history=user.get("save_history", False))


@router.post("/signup", response_model=UserPublic, status_code=201)
async def signup(body: SignupRequest, response: Response) -> UserPublic:
    email = body.email.lower()
    if await db.users.find_one({"email": email}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="An account with this email already exists. Try signing in instead.")
    user = {
        "id": str(uuid.uuid4()),
        "email": email,
        "name": body.name.strip(),
        "password_hash": await hash_password(body.password),
        "save_history": False,  # mood history is strictly opt-in
        "created_at": datetime.now(timezone.utc),
    }
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Try signing in instead.")
    await start_session(response, user["id"])
    return _public(user)


@router.post("/login", response_model=UserPublic)
async def login(body: LoginRequest, response: Response) -> UserPublic:
    user = await db.users.find_one({"email": body.email.lower()}, {"_id": 0})
    if not user or not await verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="That email and password don't match. Please try again.")
    await start_session(response, user["id"])
    return _public(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    await end_session(request, response)


@router.get("/me", response_model=AuthMeResponse)
async def me(user: dict | None = Depends(get_optional_user)) -> AuthMeResponse:
    return AuthMeResponse(user=_public(user) if user else None)


@router.patch("/settings", response_model=UserPublic)
async def update_settings(body: SettingsUpdate, user: dict = Depends(get_current_user)) -> UserPublic:
    await db.users.update_one({"id": user["id"]}, {"$set": {"save_history": body.save_history}})
    return _public({**user, "save_history": body.save_history})
