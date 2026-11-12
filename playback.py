import logging
import os
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter

from lib.db import db
from models.mood import PlaybackMatchRequest, PlaybackMatchResponse

router = APIRouter(prefix="/playback", tags=["playback"])
logger = logging.getLogger(__name__)


def _response(request: PlaybackMatchRequest, status: str, video_id: str | None = None) -> PlaybackMatchResponse:
    query = f"{request.title} {request.artist}"
    return PlaybackMatchResponse(
        title=request.title,
        artist=request.artist,
        youtube_video_id=video_id,
        youtube_url=(
            f"https://www.youtube.com/watch?v={video_id}"
            if video_id
            else f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        ),
        spotify_url=f"https://open.spotify.com/search/{quote_plus(query)}",
        matched=video_id is not None,
        using_fallback=video_id is None,
        status=status,
    )


@router.post("/match", response_model=PlaybackMatchResponse)
async def match_song(request: PlaybackMatchRequest) -> PlaybackMatchResponse:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return _response(request, "unavailable")

    # Each YouTube search costs 100 quota units, so reuse earlier answers.
    cache_key = f"{request.title.strip().lower()}|{request.artist.strip().lower()}"
    cached = await db.youtube_matches.find_one({"key": cache_key})
    if cached:
        video_id = cached.get("video_id")
        return _response(request, "matched" if video_id else "not_found", video_id)

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": f"{request.title} {request.artist}",
                    "type": "video",
                    "videoEmbeddable": "true",
                    "videoSyndicated": "true",
                    "maxResults": 1,
                    "key": api_key,
                },
            )
        response.raise_for_status()
        items = response.json().get("items", [])
    except (httpx.HTTPError, ValueError) as exc:
        # Quota exhaustion, bad key or network trouble: keep the song and offer search links.
        logger.warning("YouTube match unavailable: %s", exc)
        return _response(request, "unavailable")

    video_id = items[0].get("id", {}).get("videoId") if items else None
    await db.youtube_matches.update_one(
        {"key": cache_key}, {"$set": {"key": cache_key, "video_id": video_id}}, upsert=True
    )
    return _response(request, "matched" if video_id else "not_found", video_id)
