from datetime import datetime

from pydantic import BaseModel, Field


class SharedSong(BaseModel):
    # Only these two fields are ever stored or shown. Unknown fields (reason, mood, ...) are dropped by Pydantic.
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(min_length=1, max_length=200)


class ShareCreateRequest(BaseModel):
    songs: list[SharedSong] = Field(min_length=1, max_length=20)


class SharedMix(BaseModel):
    """Owner's view: lets them copy or delete the link."""

    id: str
    songs: list[SharedSong]
    created_at: datetime


class PublicMix(BaseModel):
    """What anyone with the link sees: the song list and nothing else."""

    songs: list[SharedSong]
