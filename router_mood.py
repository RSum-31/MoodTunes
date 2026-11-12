import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage

from lib.auth import get_optional_user
from lib.db import db
from lib.song_filters import CANONICAL_GENRES, has_hard_filters, matches, prompt_rules, shortfall_note
from models.library import FavoriteSong, TasteProfile
from models.mood import (
    MoodAnalysis,
    MoodRecommendRequest,
    MoodRecommendResponse,
    SafetyInfo,
    SongRecommendation,
)
from routers.library import load_favorites, load_taste_profile

router = APIRouter(prefix="/mood", tags=["mood"])
logger = logging.getLogger(__name__)

TARGET = 10
EXTRA_LLM_ROUNDS = 2  # after the first call, ask again up to twice when filters reject songs


def _song(title, artist, language, genre, year, tags, energy, vocal, direction) -> dict:
    return {"title": title, "artist": artist, "language": language, "genre": genre, "year": year,
            "genre_tags": tags, "energy": energy, "vocal_type": vocal, "direction": direction}


# Used only when Claude is unavailable. Every entry carries verified tags so hard filters apply to it too.
FALLBACK_SONGS = [
    _song("Holocene", "Bon Iver", "English", "Indie folk", 2011, ["Folk", "Alternative"], "low", "vocal", "comfort"),
    _song("Bloom", "The Paper Kites", "English", "Indie folk", 2013, ["Folk"], "low", "vocal", "comfort"),
    _song("Kasoor", "Prateek Kuhad", "Hindi", "Indie pop", 2017, ["Indie pop", "Pop"], "low", "vocal", "comfort"),
    _song("Gallan Goodiyaan", "Shankar-Ehsaan-Loy", "Hindi", "Bollywood pop", 2015, ["Pop", "Film melody"], "high", "vocal", "lift"),
    _song("Deewangi Deewangi", "Vishal-Shekhar", "Hindi", "Bollywood pop", 2007, ["Pop"], "high", "vocal", "lift"),
    _song("Kun Faya Kun", "A. R. Rahman", "Hindi", "Sufi devotional", 2011, ["Devotional", "Film melody"], "low", "vocal", "comfort"),
    _song("Tum Hi Ho", "Arijit Singh", "Hindi", "Film melody", 2013, ["Film melody"], "low", "vocal", "comfort"),
    _song("Inkem Inkem Inkem Kaavaale", "Sid Sriram", "Telugu", "Film pop", 2018, ["Pop", "Film melody"], "medium", "vocal", "lift"),
    _song("Butta Bomma", "Armaan Malik", "Telugu", "Film pop", 2020, ["Pop"], "high", "vocal", "lift"),
    _song("Munbe Vaa", "Shreya Ghoshal", "Tamil", "Film melody", 2006, ["Film melody"], "low", "vocal", "comfort"),
    _song("Yellow", "Coldplay", "English", "Alternative rock", 2000, ["Alternative", "Rock"], "medium", "vocal", "comfort"),
    _song("A Sky Full of Stars", "Coldplay", "English", "Pop rock", 2014, ["Pop", "Rock", "Electronic"], "high", "vocal", "lift"),
    _song("Vienna", "Billy Joel", "English", "Soft rock", 1977, ["Rock", "Pop"], "low", "vocal", "comfort"),
    _song("Sweet Disposition", "The Temper Trap", "English", "Indie rock", 2008, ["Alternative", "Rock"], "high", "vocal", "lift"),
    _song("Dog Days Are Over", "Florence + The Machine", "English", "Indie pop", 2008, ["Indie pop", "Pop"], "high", "vocal", "lift"),
    _song("An Ending (Ascent)", "Brian Eno", "English", "Ambient", 1983, ["Ambient", "Electronic"], "low", "instrumental", "comfort"),
    _song("Comptine d'un autre été", "Yann Tiersen", "French", "Film score", 2001, ["Classical"], "low", "instrumental", "comfort"),
    _song("Experience", "Ludovico Einaudi", "Italian", "Contemporary classical", 2013, ["Classical"], "medium", "instrumental", "lift"),
]


def _compact_text(value: str, limit: int = 110) -> str:
    return " ".join(value.split())[:limit]


def _fallback_analysis(text: str, quick_picks: list[str]) -> MoodAnalysis:
    combined = f"{text} {' '.join(quick_picks)}".lower()
    if any(word in combined for word in ("heartbreak", "broken", "breakup", "cry", "sad")):
        return MoodAnalysis(primary_emotion="heartbroken", secondary_emotions=["tender", "reflective"], energy_level="low",
                            situation_context="heartbreak", user_need="comfort",
                            summary="Sounds like you are carrying something tender and could use a little company.")
    if any(word in combined for word in ("anxious", "stress", "overwhelmed", "panic")):
        return MoodAnalysis(primary_emotion="anxious", secondary_emotions=["tired", "hopeful"], energy_level="low",
                            situation_context="stress", user_need="calm",
                            summary="Sounds like you are stressed but still looking for a steadier moment.")
    if any(word in combined for word in ("festive", "diwali", "excited", "happy", "celebrat", "motivated", "party")):
        return MoodAnalysis(primary_emotion="festive", secondary_emotions=["joyful", "energized"], energy_level="high",
                            situation_context="celebration", user_need="motivation",
                            summary="You sound ready for music that keeps the celebration glowing.")
    return MoodAnalysis(primary_emotion="reflective", secondary_emotions=["curious", "hopeful"], energy_level="medium",
                        situation_context="everyday life", user_need="validation",
                        summary="Sounds like you are making space for whatever this moment is asking of you.")


def _safety_for(text: str) -> SafetyInfo:
    lowered = text.lower()
    serious_terms = ("kill myself", "suicide", "self harm", "self-harm", "end my life", "hurt myself",
                     "don't want to live", "do not want to live")
    if any(term in lowered for term in serious_terms):
        return SafetyInfo(
            flagged=True,
            message="I’m really sorry this feels so heavy. You deserve immediate, human support alongside any music. If you may act on these thoughts, contact local emergency services or a crisis line now.",
            resource_label="988 Suicide & Crisis Lifeline",
            resource_url="https://988lifeline.org/",
        )
    return SafetyInfo()


def _json_from_text(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("Claude did not return a JSON object")
    # raw_decode stops at the end of the first object, so trailing commentary can't break parsing.
    parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    return parsed


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


def _allowed_artists(profile: TasteProfile, favorites: list[FavoriteSong]) -> set[str]:
    return {_norm(a) for a in profile.artists} | {_norm(f.artist) for f in favorites}


def _by_allowed_artist(song: SongRecommendation, allowed: set[str]) -> bool:
    artist = _norm(song.artist)  # "Artist feat. Someone" still counts as the listener's artist
    return any(name and (name in artist or artist in name) for name in allowed)


def _taste_prompt(profile: TasteProfile, favorites: list[FavoriteSong]) -> str:
    saved = [f"{f.title} – {f.artist}" for f in favorites[:40]]
    return f"""
MY MUSIC MODE: this listener wants songs they already love, not discoveries.
Favorite artists: {profile.artists}
Favorite songs (free text): {profile.songs}
Saved favorites: {saved}
Favorite genres: {profile.genres}
Favorite languages: {profile.languages}
Prefer songs straight from their favorite songs or saved favorites; every other song must be a real song by one of
their favorite artists or by an artist from their saved favorites. Never introduce a new artist.
"""


def _discover_prompt(known: list[FavoriteSong], surprise: bool) -> str:
    known_titles = [f"{f.title} – {f.artist}" for f in known[:40]]
    widen = "SURPRISE ME is on: deliberately range wider than usual across genres, languages and eras (still obeying any hard filters)." if surprise else ""
    return f"""
DISCOVER MODE: recommend songs this listener has probably not heard yet. Mix artists, eras and languages, and lean toward
lesser-known gems over the most obvious hits. {widen}
They already know these, so do not recommend them: {known_titles}
"""


# The shared LLM key allows one in-flight request at a time; queue calls instead of failing them.
_llm_slot = asyncio.Semaphore(1)


async def _ask_claude(prompt: str) -> str:
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise RuntimeError("EMERGENT_LLM_KEY is not configured")
    async with _llm_slot:
        for attempt in range(3):
            try:
                chat = (
                    LlmChat(
                        api_key=key,
                        session_id=f"moodtunes-{uuid.uuid4()}",
                        system_message="Return strict JSON for MoodTunes. Never include markdown fences or commentary.",
                    )
                    .with_model("anthropic", "claude-sonnet-5-5")
                    # The model spends part of its budget on hidden reasoning; a low cap truncates the JSON mid-string.
                    .with_params(max_tokens=16000)
                )
                # Non-streaming: the full reply arrives at once, so strict JSON is never cut mid-string.
                return await chat.send_message(UserMessage(text=prompt))
            except Exception as exc:
                message = str(exc)
                # Retry only the shared key's "one request at a time" limit; a spent budget won't recover in seconds.
                transient = "Budget has been exceeded" not in message and (
                    "429" in message or "RateLimit" in type(exc).__name__ or "concurrent_request_limit" in message
                )
                if attempt < 2 and transient:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise
    raise RuntimeError("Claude did not respond")


async def _claude_batch(
    request: MoodRecommendRequest, mode_prompt: str, need: int, avoid: list[str], prior: MoodAnalysis | None
) -> tuple[MoodAnalysis, list[SongRecommendation]]:
    existing = prior.model_dump_json() if prior else "null"
    prompt = f"""
You are MoodTunes, an emotionally intelligent music curator. Return ONLY valid JSON, with no markdown.
The JSON shape must be:
{{
  "analysis": {{"primary_emotion":"string","secondary_emotions":["string"],"energy_level":"low|medium|high","situation_context":"string","user_need":"comfort|motivation|calm|distraction|validation","summary":"one warm sentence"}},
  "songs": [{{"title":"string","artist":"string","language":"string","genre":"string","genre_tags":["from the canonical list"],"year":2000,"energy":"low|medium|high","vocal_type":"vocal|instrumental","direction":"comfort|lift","reason":"one sentence referencing the user's words"}}]
}}

Only real, released songs. Do not invent titles, artists, or years. For every song give VERIFIED facts:
- language: the language the lyrics are sung in. For an instrumental, the language of the music industry it comes from (e.g. "Hindi" for a Bollywood instrumental), never "Instrumental".
- genre: the specific genre; genre_tags: every matching tag from this canonical list {CANONICAL_GENRES}.
- year: the original release year. energy: how energetic the track sounds. vocal_type: whether it has sung vocals.
- direction: "comfort" if it meets the feeling gently, "lift" if it raises the mood.
The user's mood text drives the picks: the songs must fit their moment (e.g. festive songs for a festival), within the filters.
The reason must connect to the user's own words without repeating private details unnecessarily.
Use the existing analysis when it is not null (the user may have corrected its summary; honour that correction); otherwise infer it from the text.
{prompt_rules(request.filters)}
{mode_prompt}
Return {need} songs (fewer only if not enough real songs satisfy every hard filter).
User text: {request.mood_text}
Quick picks: {request.quick_picks}
Existing analysis: {existing}
Do not include any of these titles: {avoid}
They gave a thumbs-up to these, so lean toward a similar feel: {request.liked_titles}
"""
    parsed = _json_from_text(await _ask_claude(prompt))
    analysis = prior or MoodAnalysis.model_validate(parsed["analysis"])
    songs: list[SongRecommendation] = []
    for raw in parsed.get("songs", []):
        try:
            songs.append(SongRecommendation.model_validate(raw))
        except ValueError:
            continue  # a malformed entry is dropped, never repaired by guessing
    return analysis, songs


async def _curate_with_claude(
    request: MoodRecommendRequest, mode_prompt: str, allowed_song
) -> tuple[MoodAnalysis, list[SongRecommendation]]:
    """First call plus up to EXTRA_LLM_ROUNDS top-ups; every song is validated against the hard filters."""
    picked: list[SongRecommendation] = []
    rejected: list[str] = []
    analysis = request.prior_analysis
    for round_no in range(1 + EXTRA_LLM_ROUNDS):
        need = TARGET - len(picked)
        avoid = request.avoid_titles + [s.title for s in picked] + rejected
        try:
            analysis, batch = await _claude_batch(request, mode_prompt, need, avoid, analysis)
        except Exception:
            if round_no == 0:
                raise  # nothing from Claude at all -> caller uses the fallback catalog
            logger.warning("Claude top-up round %s failed; keeping %s validated songs", round_no, len(picked))
            break
        seen = {(_norm(s.title), _norm(s.artist)) for s in picked}
        for song in batch:
            key = (_norm(song.title), _norm(song.artist))
            if key in seen or _norm(song.title) in {_norm(t) for t in request.avoid_titles}:
                continue
            if matches(song, request.filters) and allowed_song(song):
                picked.append(song)
                seen.add(key)
            else:
                rejected.append(song.title)
            if len(picked) >= TARGET:
                break
        if rejected:
            logger.info("Filter validator rejected %s songs in round %s", len(rejected), round_no)
        if len(picked) >= TARGET:
            break
    assert analysis is not None
    return analysis, picked[:TARGET]


def _fallback_pool(request: MoodRecommendRequest, analysis: MoodAnalysis, allowed_song, favorites: list[FavoriteSong]) -> list[SongRecommendation]:
    avoids = {_norm(t) for t in request.avoid_titles}
    mood_quote = _compact_text(request.mood_text, 70)
    pool: list[SongRecommendation] = []
    if request.mode == "my_music":
        for fav in favorites:
            pool.append(SongRecommendation(**{**fav.model_dump(include=set(SongRecommendation.model_fields)),
                                              "reason": f"One of your own favorites, for the {analysis.primary_emotion} feeling you described."}))
    for item in FALLBACK_SONGS:
        pool.append(SongRecommendation(**item, reason=f"Because you said “{mood_quote}”, this fits your need for {analysis.user_need}."))
    picks: list[SongRecommendation] = []
    for song in pool:
        if _norm(song.title) in avoids or any(_norm(p.title) == _norm(song.title) for p in picks):
            continue
        if matches(song, request.filters) and allowed_song(song):  # never padded with non-matching songs
            picks.append(song)
        if len(picks) == TARGET:
            break
    return picks


async def _save_history(user: dict, request: MoodRecommendRequest, analysis: MoodAnalysis, songs: list[SongRecommendation]) -> None:
    await db.mood_history.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "mood_text": request.mood_text,
        "quick_picks": request.quick_picks,
        "mode": request.mode,
        "primary_emotion": analysis.primary_emotion,
        "summary": analysis.summary,
        "song_titles": [f"{s.title} – {s.artist}" for s in songs],
        "created_at": datetime.now(timezone.utc),
    })


@router.post("/recommend", response_model=MoodRecommendResponse)
async def recommend_mood(request: MoodRecommendRequest, user: dict | None = Depends(get_optional_user)) -> MoodRecommendResponse:
    safety = _safety_for(request.mood_text)
    favorites = await load_favorites(user["id"], limit=100) if user else []

    if request.mode == "my_music":
        if not user:
            raise HTTPException(status_code=401, detail="Sign in to use My Music.")
        profile = await load_taste_profile(user["id"])
        allowed = _allowed_artists(profile, favorites)
        if not allowed and not profile.songs:
            raise HTTPException(status_code=400, detail="Add a few favorite artists or songs to your taste profile first.")
        mode_prompt = _taste_prompt(profile, favorites)

        def allowed_song(song: SongRecommendation) -> bool:
            # Enforce My Music server-side: only the listener's own artists or listed songs.
            return _by_allowed_artist(song, allowed) or any(_norm(song.title) in _norm(t) for t in profile.songs)
    else:
        mode_prompt = _discover_prompt(favorites, request.filters.surprise)

        def allowed_song(song: SongRecommendation) -> bool:
            return True

    try:
        analysis, songs = await _curate_with_claude(request, mode_prompt, allowed_song)
        engine = "claude"
    except Exception as exc:
        logger.warning("Claude recommendation fallback: %s", exc)
        analysis = request.prior_analysis or _fallback_analysis(request.mood_text, request.quick_picks)
        songs = _fallback_pool(request, analysis, allowed_song, favorites)
        engine = "fallback_catalog"

    filter_note = None
    if len(songs) < TARGET and has_hard_filters(request.filters):
        filter_note = shortfall_note(len(songs), analysis.primary_emotion.lower(), request.filters)
    elif not songs:
        filter_note = "We couldn't find songs for this moment yet. Try rewording how you feel."

    history_saved = False
    if user and user.get("save_history") and request.record_history:
        await _save_history(user, request, analysis, songs)
        history_saved = True
    return MoodRecommendResponse(analysis=analysis, songs=songs, safety=safety, engine=engine,
                                 history_saved=history_saved, filter_note=filter_note)
