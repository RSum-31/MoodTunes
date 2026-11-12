import re

from models.mood import MoodFilters, SongRecommendation

# Kept in sync with GENRES in frontend/src/lib/songs.ts.
CANONICAL_GENRES = [
    "Pop", "Indie pop", "Film melody", "Folk", "Devotional", "R&B", "Hip-hop",
    "Rock", "Alternative", "Electronic", "Ambient", "Classical", "Jazz", "Lo-fi",
]
ENERGY_WORDS = {"low": "calm / low energy", "medium": "steady / medium energy", "high": "upbeat / high energy"}


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


def _language_parts(language: str) -> set[str]:
    return {_norm(p) for p in re.split(r"[/,&+]|\band\b", language) if p.strip()}


def _era_range(era: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d{4})s$", era.strip())
    if not match:
        return None
    start = int(match.group(1))
    return start, start + 9


def language_ok(song: SongRecommendation, filters: MoodFilters) -> bool:
    if not filters.language:
        return True
    wanted = {_norm(lang) for lang in filters.language}
    return bool(_language_parts(song.language) & wanted)


def genre_ok(song: SongRecommendation, filters: MoodFilters) -> bool:
    if not filters.genre:
        return True
    tags = {_norm(t) for t in song.genre_tags}
    genre = _norm(song.genre)
    for wanted in filters.genre:
        w = _norm(wanted)
        # "Pop" accepts "Indie pop" / "Bollywood pop"; whole-word match so "Pop" never matches "K-popish" noise.
        if w in tags or re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", genre):
            return True
    return False


def era_ok(song: SongRecommendation, filters: MoodFilters) -> bool:
    if not filters.era:
        return True
    bounds = _era_range(filters.era)
    return bool(bounds) and song.year > 0 and bounds[0] <= song.year <= bounds[1]


def matches(song: SongRecommendation, filters: MoodFilters) -> bool:
    return (
        language_ok(song, filters)
        and genre_ok(song, filters)
        and era_ok(song, filters)
        and (filters.energy is None or song.energy == filters.energy)
        and (filters.vocal_type is None or song.vocal_type == filters.vocal_type)
        and (filters.direction is None or song.direction == filters.direction)
    )


def has_hard_filters(filters: MoodFilters) -> bool:
    return bool(filters.language or filters.genre or filters.era or filters.energy or filters.vocal_type or filters.direction)


def prompt_rules(filters: MoodFilters) -> str:
    rules: list[str] = []
    if filters.language:
        rules.append(f"Return ONLY songs whose language is in {filters.language}. Do not include any other language.")
    if filters.genre:
        rules.append(f"Return ONLY songs whose genre is one of {filters.genre} (a sub-genre such as 'Bollywood pop' counts as Pop). Do not include any other genre.")
    if filters.era:
        bounds = _era_range(filters.era)
        if bounds:
            rules.append(f"Return ONLY songs originally released between {bounds[0]} and {bounds[1]} inclusive.")
    if filters.energy:
        rules.append(f"Return ONLY songs whose energy is \"{filters.energy}\" ({ENERGY_WORDS[filters.energy]}).")
    if filters.vocal_type == "instrumental":
        rules.append('Return ONLY instrumental tracks with no sung lyrics (vocal_type "instrumental").')
    elif filters.vocal_type == "vocal":
        rules.append('Return ONLY songs with sung vocals (vocal_type "vocal").')
    if filters.direction == "comfort":
        rules.append('Return ONLY songs that meet the feeling gently and keep the listener company (direction "comfort").')
    elif filters.direction == "lift":
        rules.append('Return ONLY songs that raise the listener\'s mood (direction "lift").')
    if not rules:
        return "No hard filters are active."
    return "HARD FILTERS (every song must satisfy ALL of these; if you cannot find enough, return fewer songs rather than break a rule):\n- " + "\n- ".join(rules)


def describe(filters: MoodFilters) -> str:
    parts: list[str] = []
    if filters.language:
        parts.append(" or ".join(filters.language))
    if filters.genre:
        parts.append(" or ".join(filters.genre))
    if filters.era:
        parts.append(filters.era)
    if filters.energy:
        parts.append(ENERGY_WORDS[filters.energy].split(" / ")[0])
    if filters.vocal_type:
        parts.append(filters.vocal_type)
    return " ".join(parts)


def shortfall_note(count: int, mood_word: str, filters: MoodFilters) -> str:
    if filters.language:
        hint = "adding another language"
    elif filters.genre:
        hint = "adding another genre"
    elif filters.era:
        hint = "choosing a neighbouring era"
    else:
        hint = "loosening one filter"
    noun = "song" if count == 1 else "songs"
    if count == 0:
        return f"No {mood_word} {describe(filters)} songs found for this mood. Try {hint}."
    return f"Only {count} {mood_word} {describe(filters)} {noun} found. Try {hint}."
