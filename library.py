from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from models.mood import SongRecommendation

TagList = list[str]


class FavoriteSong(SongRecommendation):
    id: str
    created_at: datetime


class FavoritesBulkRequest(BaseModel):
    songs: list[SongRecommendation] = Field(max_length=200)


class TasteProfile(BaseModel):
    artists: TagList = Field(default_factory=list, max_length=50)
    songs: TagList = Field(default_factory=list, max_length=50)
    genres: TagList = Field(default_factory=list, max_length=30)
    languages: TagList = Field(default_factory=list, max_length=20)


class MoodHistoryEntry(BaseModel):
    id: str
    mood_text: str
    quick_picks: list[str]
    mode: Literal["discover", "my_music"]
    primary_emotion: str
    summary: str
    song_titles: list[str]
    created_at: datetime
