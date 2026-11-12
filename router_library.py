import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from lib.auth import get_current_user
from lib.db import db
from models.library import FavoriteSong, FavoritesBulkRequest, MoodHistoryEntry, TasteProfile
from models.mood import SongRecommendation

router = APIRouter(tags=["library"])


def song_key(title: str, artist: str) -> str:
    return f"{title.strip().lower()}|{artist.strip().lower()}"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _clean_tags(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        tag = " ".join(value.split())[:120]
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            cleaned.append(tag)
    return cleaned


async def _upsert_favorite(user_id: str, song: SongRecommendation) -> None:
    await db.favorites.update_one(
        {"user_id": user_id, "key": song_key(song.title, song.artist)},
        {
            "$set": song.model_dump(),
            "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def load_favorites(user_id: str, limit: int = 500) -> list[FavoriteSong]:
    docs = await db.favorites.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return [FavoriteSong(**{**doc, "created_at": _aware(doc["created_at"])}) for doc in docs]


async def load_taste_profile(user_id: str) -> TasteProfile:
    doc = await db.taste_profiles.find_one({"user_id": user_id}, {"_id": 0, "user_id": 0})
    return TasteProfile(**doc) if doc else TasteProfile()


@router.get("/favorites", response_model=list[FavoriteSong])
async def list_favorites(user: dict = Depends(get_current_user)) -> list[FavoriteSong]:
    return await load_favorites(user["id"])


@router.post("/favorites", response_model=FavoriteSong, status_code=201)
async def add_favorite(song: SongRecommendation, user: dict = Depends(get_current_user)) -> FavoriteSong:
    await _upsert_favorite(user["id"], song)
    doc = await db.favorites.find_one({"user_id": user["id"], "key": song_key(song.title, song.artist)}, {"_id": 0})
    return FavoriteSong(**{**doc, "created_at": _aware(doc["created_at"])})


@router.post("/favorites/bulk", response_model=list[FavoriteSong])
async def import_favorites(body: FavoritesBulkRequest, user: dict = Depends(get_current_user)) -> list[FavoriteSong]:
    """Moves a guest's on-device favorites into their account after sign-in."""
    for song in body.songs:
        await _upsert_favorite(user["id"], song)
    return await load_favorites(user["id"])


@router.delete("/favorites/{favorite_id}", status_code=204)
async def remove_favorite(favorite_id: str, user: dict = Depends(get_current_user)) -> None:
    result = await db.favorites.delete_one({"user_id": user["id"], "id": favorite_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="That favorite was already removed.")


@router.get("/taste-profile", response_model=TasteProfile)
async def get_taste_profile(user: dict = Depends(get_current_user)) -> TasteProfile:
    return await load_taste_profile(user["id"])


@router.put("/taste-profile", response_model=TasteProfile)
async def save_taste_profile(body: TasteProfile, user: dict = Depends(get_current_user)) -> TasteProfile:
    profile = TasteProfile(
        artists=_clean_tags(body.artists),
        songs=_clean_tags(body.songs),
        genres=_clean_tags(body.genres),
        languages=_clean_tags(body.languages),
    )
    await db.taste_profiles.update_one(
        {"user_id": user["id"]},
        {"$set": {**profile.model_dump(), "user_id": user["id"], "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return profile


@router.get("/history", response_model=list[MoodHistoryEntry])
async def list_history(user: dict = Depends(get_current_user)) -> list[MoodHistoryEntry]:
    docs = await db.mood_history.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).sort("created_at", -1).to_list(200)
    return [MoodHistoryEntry(**{**doc, "created_at": _aware(doc["created_at"])}) for doc in docs]


@router.delete("/history/{entry_id}", status_code=204)
async def delete_history_entry(entry_id: str, user: dict = Depends(get_current_user)) -> None:
    result = await db.mood_history.delete_one({"user_id": user["id"], "id": entry_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="That entry was already deleted.")


@router.delete("/history", status_code=204)
async def clear_history(user: dict = Depends(get_current_user)) -> None:
    await db.mood_history.delete_many({"user_id": user["id"]})
