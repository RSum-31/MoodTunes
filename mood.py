from typing import Literal

from pydantic import BaseModel, Field


class MoodFilters(BaseModel):
    genre: list[str] = Field(default_factory=list)
    language: list[str] = Field(default_factory=list)
    era: str | None = None
    energy: Literal["low", "medium", "high"] | None = None
    direction: Literal["comfort", "lift"] | None = None
    vocal_type: Literal["vocal", "instrumental"] | None = None
    surprise: bool = False


class MoodAnalysis(BaseModel):
    primary_emotion: str
    secondary_emotions: list[str] = Field(default_factory=list)
    energy_level: Literal["low", "medium", "high"]
    situation_context: str
    user_need: str
    summary: str


class SongRecommendation(BaseModel):
    title: str
    artist: str
    language: str
    genre: str
    year: int
    reason: str
    # Verified attributes used to enforce hard filters; optional because older saved favorites lack them.
    genre_tags: list[str] = Field(default_factory=list)
    energy: Literal["low", "medium", "high"] | None = None
    vocal_type: Literal["vocal", "instrumental"] | None = None
    direction: Literal["comfort", "lift"] | None = None


class SafetyInfo(BaseModel):
    flagged: bool = False
    message: str | None = None
    resource_label: str | None = None
    resource_url: str | None = None


class MoodRecommendRequest(BaseModel):
    mood_text: str = Field(min_length=3, max_length=2000)
    quick_picks: list[str] = Field(default_factory=list)
    filters: MoodFilters = Field(default_factory=MoodFilters)
    prior_analysis: MoodAnalysis | None = None
    avoid_titles: list[str] = Field(default_factory=list)
    liked_titles: list[str] = Field(default_factory=list)
    mode: Literal["discover", "my_music"] = "discover"
    # True only for a fresh submission; filter tweaks and regenerations never add history rows.
    record_history: bool = False


class MoodRecommendResponse(BaseModel):
    analysis: MoodAnalysis
    songs: list[SongRecommendation]
    safety: SafetyInfo
    engine: Literal["claude", "fallback_catalog"]
    history_saved: bool = False
    # Set when fewer than 10 songs satisfy the hard filters; the list is never padded with non-matching songs.
    filter_note: str | None = None


class PlaybackMatchRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(min_length=1, max_length=200)


class PlaybackMatchResponse(BaseModel):
    title: str
    artist: str
    youtube_video_id: str | None = None
    youtube_url: str
    spotify_url: str
    matched: bool
    using_fallback: bool
    # matched = real video; not_found = key works but no video exists; unavailable = no key, quota or network error
    status: Literal["matched", "not_found", "unavailable"]
