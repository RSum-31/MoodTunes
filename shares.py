import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from lib.auth import get_current_user
from lib.db import db
from models.share import PublicMix, ShareCreateRequest, SharedMix

router = APIRouter(tags=["shares"])


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@router.post("/shares", response_model=SharedMix, status_code=201)
async def create_share(body: ShareCreateRequest, user: dict = Depends(get_current_user)) -> SharedMix:
    mix = SharedMix(
        id=secrets.token_urlsafe(16),  # 128 random bits; never derived from the user or the mood
        songs=body.songs,
        created_at=datetime.now(timezone.utc),
    )
    await db.shares.insert_one({**mix.model_dump(), "owner_id": user["id"]})
    return mix


@router.get("/shares", response_model=list[SharedMix])
async def list_shares(user: dict = Depends(get_current_user)) -> list[SharedMix]:
    docs = await db.shares.find({"owner_id": user["id"]}, {"_id": 0, "owner_id": 0}).sort("created_at", -1).to_list(200)
    return [SharedMix(**{**d, "created_at": _aware(d["created_at"])}) for d in docs]


@router.delete("/shares/{share_id}", status_code=204)
async def delete_share(share_id: str, user: dict = Depends(get_current_user)) -> None:
    result = await db.shares.delete_one({"id": share_id, "owner_id": user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="That link was already deleted.")


@router.get("/mixes/{share_id}", response_model=PublicMix)
async def get_public_mix(share_id: str) -> PublicMix:
    doc = await db.shares.find_one({"id": share_id}, {"_id": 0, "songs": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="This mix link was deleted or never existed.")
    return PublicMix(songs=doc["songs"])
