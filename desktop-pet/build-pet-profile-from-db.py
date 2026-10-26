#!/usr/bin/env python3
"""从本地 SQLite 数据库读取「最新已选择」的音灵候选，生成 pet-profile.json。

与 build-pet-profile.py 不同，本脚本不依赖 demo-session.json / API 凭证，
而是直接读取后端运行库 backend/runtime/melopet.db 中最新的选择记录，
因此能对应「你在网页上刚生成并选择的那只音灵」。

用法（在验证包根目录执行，路径会自动解析）：

    ./desktop-pet/build-pet-profile-from-db.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT, "backend")
DESKTOP_DIR = os.path.join(ROOT, "desktop-pet")
DB_FILE = os.path.join(BACKEND_DIR, "runtime", "melopet.db")
PROFILE_FILE = os.path.join(DESKTOP_DIR, "pet-profile.json")

# 与 backend/src/melopet_backend/dna.py 中的 MOOD_ENERGY 保持一致。
MOOD_ENERGY = {
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

MOTION_NAMES = {
    "motion_bounce_dev": "活泼",
    "motion_float_dev": "灵动",
    "motion_breathe_dev": "安静",
}

SPECIES_NAMES = {
    "species_cat_dev": "猫咪",
    "species_fox_dev": "狐狸",
    "species_bird_dev": "小鸟",
    "species_bunny_dev": "兔子",
    "species_bear_dev": "小熊",
    "species_spirit_dev": "星灵",
}

ACCESSORY_NAMES = {
    "accessory_star_dev": "星星发饰",
    "accessory_note_dev": "音符发饰",
    "accessory_ribbon_dev": "蝴蝶结",
    "accessory_halo_dev": "光环",
    "accessory_headphone_dev": "大耳机",
}


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _asset_file(catalog: dict, asset_id: str) -> str:
    for asset in catalog["assets"]:
        if asset.get("assetId") == asset_id:
            uri = asset["uri"]
            if uri.startswith("inline://"):
                raise SystemExit(f"资产 {asset_id} 是内联资产，无法解析。")
            return os.path.join(BACKEND_DIR, "assets", uri)
    raise SystemExit(f"资产目录中找不到 {asset_id}")


def _resolve_svg(path: str, replacements: dict[str, str] | None = None) -> str:
    with open(path, encoding="utf-8") as handle:
        raw = handle.read()
    for token, value in (replacements or {}).items():
        raw = raw.replace(token, value)
    return raw


def main() -> int:
    if not os.path.exists(DB_FILE):
        raise SystemExit(f"未找到数据库：{DB_FILE}")

    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    try:
        selection = connection.execute(
            "SELECT * FROM pet_selections ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not selection:
            raise SystemExit("数据库中还没有任何选择记录，请先在验收台完成候选选择。")

        candidate = connection.execute(
            "SELECT * FROM pet_candidates WHERE id = ?",
            (selection["candidate_record_id"],),
        ).fetchone()
        if not candidate:
            raise SystemExit(f"找不到已选择的候选：{selection['candidate_record_id']}")

        spec = json.loads(candidate["spec_json"])
        copy = json.loads(candidate["copy_json"])

        profile = connection.execute(
            "SELECT * FROM mood_profiles WHERE revision = ?",
            (spec.get("profileRevision") or candidate["profile_revision"],),
        ).fetchone()
        if not profile:
            raise SystemExit("找不到候选对应的情绪画像。")
        percentages = json.loads(profile["percentages_json"])
    finally:
        connection.close()

    mapping = _load_json(os.path.join(BACKEND_DIR, "catalogs", "mapping-rules.v0.1.json"))
    taxonomy = _load_json(os.path.join(BACKEND_DIR, "catalogs", "mood-taxonomy.v0.1.json"))
    catalog = _load_json(os.path.join(BACKEND_DIR, "catalogs", "asset-catalog.dev-0.1.json"))

    body_color, accent_color = mapping["paletteColors"][spec["bodyPaletteId"]]
    species_svg = _resolve_svg(
        _asset_file(catalog, spec["speciesId"]),
        {"__BODY__": body_color, "__ACCENT__": accent_color},
    )
    accessory_id = spec["accessoryIds"][0] if spec["accessoryIds"] else None
    accessory_svg = _resolve_svg(_asset_file(catalog, accessory_id)) if accessory_id else ""
    headphone_svg = _resolve_svg(_asset_file(catalog, "accessory_headphone_dev"))

    mood_by_id = {item["moodId"]: item for item in taxonomy["taxonomy"]}
    ordered = sorted(
        [item for item in percentages if item["moodId"] != "UNKNOWN"],
        key=lambda item: -int(item["percent"]),
    )
    primary_id = ordered[0]["moodId"] if ordered else "UNKNOWN"
    primary_percent = int(ordered[0]["percent"]) if ordered else 0
    primary_name = mood_by_id.get(primary_id, {}).get("displayName", primary_id)

    total = sum(int(item["percent"]) for item in percentages)
    energy = round(
        sum(MOOD_ENERGY.get(item["moodId"], 50) * int(item["percent"]) for item in percentages) / total
    ) if total else 50

    profile_out = {
        "name": copy.get("petName", "小宠"),
        "intro": copy.get("intro", ""),
        "clickLines": copy.get("clickLines", []),
        "speciesId": spec["speciesId"],
        "speciesName": SPECIES_NAMES.get(spec["speciesId"], spec["speciesId"]),
        "motionId": spec.get("idleMotionId") or "motion_breathe_dev",
        "motionName": MOTION_NAMES.get(spec.get("idleMotionId"), "安静"),
        "accessoryId": accessory_id,
        "accessoryName": ACCESSORY_NAMES.get(accessory_id, "") if accessory_id else "",
        "bodyColor": body_color,
        "accentColor": accent_color,
        "primaryMoodId": primary_id,
        "primaryMoodName": primary_name,
        "primaryMoodPercent": primary_percent,
        "energy": energy,
        "speciesSvg": species_svg,
        "accessorySvg": accessory_svg,
        "headphoneSvg": headphone_svg,
        "source": {
            "petTaskId": candidate["task_id"],
            "candidateRecordId": candidate["id"],
            "profileRevision": candidate["profile_revision"],
        },
    }
    with open(PROFILE_FILE, "w", encoding="utf-8") as handle:
        json.dump(profile_out, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(PROFILE_FILE, 0o644)

    print(f"✅ 已生成 {PROFILE_FILE}")
    print(
        f"   音灵：{profile_out['name']}（{profile_out['speciesName']} · "
        f"{profile_out['motionName']} · {profile_out['primaryMoodName']} {profile_out['primaryMoodPercent']}%）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
