import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING, IndexModel

load_dotenv(Path(__file__).parent.parent / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

logger = logging.getLogger(__name__)

# One entry per collection: every field a route filters, sorts, or dedupes on. Applied by ensure_indexes() at startup.
INDEXES: dict[str, list[IndexModel]] = {
    "status_checks": [IndexModel([("timestamp", DESCENDING)], name="timestamp_desc")],
    "youtube_matches": [IndexModel([("key", ASCENDING)], name="key", unique=True)],
    "users": [
        IndexModel([("id", ASCENDING)], name="id", unique=True),
        IndexModel([("email", ASCENDING)], name="email", unique=True),
    ],
    "sessions": [
        IndexModel([("token_hash", ASCENDING)], name="token_hash", unique=True),
        IndexModel([("expires_at", ASCENDING)], name="expires_ttl", expireAfterSeconds=0),
    ],
    "favorites": [
        IndexModel([("user_id", ASCENDING), ("key", ASCENDING)], name="user_key", unique=True),
        IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created"),
        IndexModel([("user_id", ASCENDING), ("id", ASCENDING)], name="user_id_id"),
    ],
    "taste_profiles": [IndexModel([("user_id", ASCENDING)], name="user_id", unique=True)],
    "shares": [
        IndexModel([("id", ASCENDING)], name="id", unique=True),
        IndexModel([("owner_id", ASCENDING), ("created_at", DESCENDING)], name="owner_created"),
    ],
    "mood_history": [
        IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created"),
        IndexModel([("user_id", ASCENDING), ("id", ASCENDING)], name="user_id_id"),
    ],
}


async def ensure_indexes() -> None:
    for collection, models in INDEXES.items():
        for model in models:  # one at a time so a bad spec skips only itself
            try:
                await db[collection].create_indexes([model])
            except Exception as exc:  # never block boot on an index; the log line names what to fix
                logger.error("ensure_indexes(%s.%s): %s", collection, model.document["name"], exc)
