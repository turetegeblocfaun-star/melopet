from __future__ import annotations

from typing import Any

from .catalogs import CatalogBundle

# Rule-based fallbacks used when no external model is configured. The PRD
# (Playlist Pet V1.0) requires a "Playlist DNA" with genres, mood vector,
# energy, keywords, one-sentence personality, representative tracks and an
# explanation. These tables let the rule-only path still produce a useful,
# deterministic DNA instead of leaving the profile empty.

MOOD_ENERGY: dict[str, int] = {
    "heroic_drive": 84,
    "joy_bright": 74,
    "romantic_tender": 48,
    "healing_warm": 40,
    "calm_focus": 24,
    "mysterious_dark": 42,
    "melancholy_sad": 20,
    "nostalgic_story": 32,
    "UNKNOWN": 50,
}

MOOD_GENRE_HINTS: dict[str, str] = {
    "heroic_drive": "摇滚 / 高能",
    "joy_bright": "流行 / 明亮舞曲",
    "healing_warm": "民谣 / 舒缓流行",
    "romantic_tender": "情歌 / 抒情流行",
    "melancholy_sad": "伤感民谣 / 慢歌",
    "calm_focus": "氛围 / Lo-fi",
    "mysterious_dark": "暗黑 / 电子",
    "nostalgic_story": "怀旧 / 老歌",
    "UNKNOWN": "多元风格",
}

MOOD_KEYWORDS: dict[str, list[str]] = {
    "heroic_drive": ["热血", "昂扬", "力量感"],
    "joy_bright": ["明亮", "轻快", "活力"],
    "healing_warm": ["治愈", "温柔", "包裹感"],
    "romantic_tender": ["浪漫", "柔情", "细腻"],
    "melancholy_sad": ["感伤", "内省", "安静"],
    "calm_focus": ["平静", "专注", "克制"],
    "mysterious_dark": ["神秘", "冷峻", "暗色"],
    "nostalgic_story": ["怀旧", "叙事", "年代感"],
    "UNKNOWN": ["多元", "待探索", "混搭"],
}

MOOD_SUMMARIES: dict[str, str] = {
    "heroic_drive": "表面热血，耳机里永远在打一场自己的仗。",
    "joy_bright": "明亮得像晴天，走到哪都自带欢快 BGM。",
    "healing_warm": "温柔包裹一切，慢热但让人安心。",
    "romantic_tender": "细腻浪漫，把心事都藏进旋律里。",
    "melancholy_sad": "安静内敛，喜欢把情绪泡在低回的旋律里。",
    "calm_focus": "平静专注，越安静越有力量。",
    "mysterious_dark": "神秘冷峻，像夜里一盏不明所以的光。",
    "nostalgic_story": "怀旧恋旧，收藏着旧时光的每个片段。",
    "UNKNOWN": "口味多样，像一本还没翻完的歌单。",
}


def _mood_name(mood_id: str, catalogs: CatalogBundle) -> str:
    return catalogs.taxonomy_by_id.get(mood_id, {}).get("displayName", mood_id)


def _top_moods(percentages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [item for item in percentages if item.get("moodId") != "UNKNOWN"]
    return sorted(ordered, key=lambda item: -int(item.get("percent", 0)))


def build_dna(
    *,
    percentages: list[dict[str, Any]],
    representative_tracks: list[dict[str, Any]],
    catalogs: CatalogBundle,
) -> dict[str, Any]:
    """Build a PRD-aligned playlist DNA from a mood profile.

    The output is deterministic and does not require an external model; the
    optional LLM path can later override ``genres`` and ``summary``.
    """
    top = _top_moods(percentages)
    primary = top[0]["moodId"] if top else "UNKNOWN"
    primary_percent = int(top[0].get("percent", 0)) if top else 0

    weighted = sum(
        MOOD_ENERGY.get(item.get("moodId", "UNKNOWN"), 50) * int(item.get("percent", 0))
        for item in percentages
    )
    total = sum(int(item.get("percent", 0)) for item in percentages)
    energy = round(weighted / total) if total else 50
    energy = max(0, min(100, energy))

    genres: list[str] = []
    for item in top[:3]:
        hint = MOOD_GENRE_HINTS.get(item["moodId"], "多元风格")
        if hint not in genres:
            genres.append(hint)
    if not genres:
        genres = ["多元风格"]

    keywords: list[str] = []
    for item in top[:3]:
        for keyword in MOOD_KEYWORDS.get(item["moodId"], []):
            if keyword not in keywords:
                keywords.append(keyword)
            if len(keywords) >= 6:
                break
        if len(keywords) >= 6:
            break
    if not keywords:
        keywords = ["多元", "待探索"]

    summary = MOOD_SUMMARIES.get(primary, MOOD_SUMMARIES["UNKNOWN"])

    if primary != "UNKNOWN" and primary_percent:
        explanation = f"这份歌单最突出的是「{_mood_name(primary, catalogs)}」（{primary_percent}%）。"
        if representative_tracks:
            names = "、".join(f"《{track['title']}》" for track in representative_tracks[:3])
            explanation += f"代表曲目包括 {names}。"
    else:
        explanation = "当前信号不足，先按「待确认」处理；逐曲纠正后重新生成画像即可得到更清晰的 DNA。"

    return {
        "genres": genres,
        "energy": energy,
        "keywords": keywords,
        "summary": summary,
        "representativeTracks": [
            {"title": track.get("title", ""), "artist": track.get("artist", "")}
            for track in representative_tracks
        ],
        "explanation": explanation,
    }
