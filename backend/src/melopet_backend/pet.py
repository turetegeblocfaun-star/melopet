from __future__ import annotations

import base64
import html
from dataclasses import dataclass
from typing import Any

from .catalogs import CatalogBundle
from .config import Settings
from .errors import AppError
from .model_gateway import ModelCallResult, ModelGateway
from .object_store import LocalObjectStore
from .schemas import PetCopyOutput, PetCustomization
from .util import sha256_bytes


@dataclass(slots=True)
class PetCandidateResult:
    candidate_id: str
    spec: dict[str, Any]
    copy: dict[str, Any]
    preview_ref: str


FORBIDDEN_COPY = ("你就是", "你一定", "你患有", "真实性格", "抑郁", "焦虑症")

SPECIES_DISPLAY_NAMES = {
    "species_cat_dev": "猫咪",
    "species_fox_dev": "狐狸",
    "species_bird_dev": "小鸟",
    "species_bunny_dev": "兔子",
    "species_bear_dev": "小熊",
    "species_spirit_dev": "星灵",
}

PALETTE_DISPLAY_NAMES = {
    "palette_crimson_dev": "绯红",
    "palette_sun_dev": "暖阳",
    "palette_mint_dev": "薄荷",
    "palette_rose_dev": "玫瑰",
    "palette_blue_dev": "天蓝",
    "palette_slate_dev": "雾灰",
    "palette_violet_dev": "紫罗兰",
    "palette_amber_dev": "琥珀",
}

MOTION_DISPLAY_NAMES = {
    "motion_bounce_dev": "活泼",
    "motion_float_dev": "灵动",
    "motion_breathe_dev": "安静",
}

ACCESSORY_DISPLAY_NAMES = {
    "accessory_star_dev": "星星发饰",
    "accessory_note_dev": "音符发饰",
    "accessory_ribbon_dev": "蝴蝶结",
    "accessory_halo_dev": "光环",
    "accessory_headphone_dev": "大耳机",
}

HEADPHONE_ASSET_ID = "accessory_headphone_dev"


def customization_catalog(catalogs: CatalogBundle) -> dict[str, Any]:
    """Return the user-facing pet customization options for the UI."""
    species = [
        {"id": asset_id, "name": SPECIES_DISPLAY_NAMES.get(asset_id, asset_id)}
        for asset_id, asset in catalogs.assets.items()
        if asset["type"] == "species" and asset["enabled"]
    ]
    palettes = [
        {
            "id": asset_id,
            "name": PALETTE_DISPLAY_NAMES.get(asset_id, asset_id),
            "colors": catalogs.mapping.get("paletteColors", {}).get(asset_id, []),
        }
        for asset_id, asset in catalogs.assets.items()
        if asset["type"] == "palette" and asset["enabled"]
    ]
    motions = [
        {"id": asset_id, "name": MOTION_DISPLAY_NAMES.get(asset_id, asset_id)}
        for asset_id, asset in catalogs.assets.items()
        if asset["type"] == "motion" and asset["enabled"]
    ]
    return {
        "version": catalogs.asset_catalog_version,
        "species": species,
        "palettes": palettes,
        "motions": motions,
        "headphone": {"id": HEADPHONE_ASSET_ID, "name": "佩戴耳机"},
    }


def validate_customization(customization: PetCustomization | None, catalogs: CatalogBundle) -> None:
    if customization is None:
        return
    checks = (
        ("speciesId", customization.species_id, "species"),
        ("paletteId", customization.palette_id, "palette"),
        ("motionId", customization.motion_id, "motion"),
    )
    for field, value, asset_type in checks:
        if value is None:
            continue
        asset = catalogs.assets.get(value)
        if not asset or not asset["enabled"] or asset["type"] != asset_type:
            raise AppError(
                "PET_CUSTOMIZATION_INVALID",
                "你选择的音灵选项不可用，请刷新后重试。",
                400,
                details={"field": field, "value": value},
            )
    if customization.headphone is not None:
        asset = catalogs.assets.get(HEADPHONE_ASSET_ID)
        if not asset or not asset["enabled"]:
            raise AppError("PET_CUSTOMIZATION_INVALID", "耳机配件当前不可用。", 400)


def _top_moods(profile: dict[str, Any], catalogs: CatalogBundle) -> list[dict[str, Any]]:
    order = {mood_id: index for index, mood_id in enumerate(catalogs.taxonomy_order)}
    values = [item for item in profile["percentages"] if item["moodId"] != "UNKNOWN"]
    return sorted(values, key=lambda item: (-item["percent"], order[item["moodId"]]))


def _validate_asset(asset_id: str, catalogs: CatalogBundle) -> dict[str, Any]:
    asset = catalogs.assets.get(asset_id)
    if not asset or not asset["enabled"]:
        raise ValueError(f"ASSET_INVALID:{asset_id}")
    return asset


def map_pet_spec(
    *,
    profile: dict[str, Any],
    playlist_hash: str,
    candidate_index: int,
    catalogs: CatalogBundle,
    customization: PetCustomization | None = None,
) -> dict[str, Any]:
    top = _top_moods(profile, catalogs)
    primary = top[0]["moodId"] if top else "UNKNOWN"
    secondary = top[1]["moodId"] if len(top) > 1 else primary
    seed = sha256_bytes(f"{playlist_hash}:{candidate_index}".encode())
    species_choices = catalogs.mapping["speciesByMood"][primary]
    species_id = species_choices[(int(seed[:8], 16) + candidate_index) % len(species_choices)]
    palette_id = catalogs.mapping["paletteByMood"][primary]
    motion_id = catalogs.mapping["motionByMood"][primary]
    customized: list[str] = []
    if customization is not None:
        if customization.species_id:
            species_id = customization.species_id
            customized.append("speciesId")
        if customization.palette_id:
            palette_id = customization.palette_id
            customized.append("bodyPaletteId")
        if customization.motion_id:
            motion_id = customization.motion_id
            customized.append("idleMotionId")
    accessory_pool = [
        "accessory_star_dev",
        "accessory_note_dev",
        "accessory_ribbon_dev",
        "accessory_halo_dev",
        HEADPHONE_ASSET_ID,
    ]
    if customization is not None and customization.headphone is True:
        accessory_ids = [HEADPHONE_ASSET_ID]
        customized.append("accessoryIds")
    elif customization is not None and customization.headphone is False:
        non_headphone = [asset_id for asset_id in accessory_pool if asset_id != HEADPHONE_ASSET_ID]
        accessory_ids = [non_headphone[(int(seed[8:16], 16) + candidate_index) % len(non_headphone)]]
        customized.append("accessoryIds")
    else:
        accessory_ids = [
            accessory_pool[(int(seed[8:16], 16) + candidate_index) % len(accessory_pool)]
        ]
    ids = [
        species_id,
        palette_id,
        "aura_glow_dev",
        "eyes_round_dev",
        motion_id,
        "reaction_spark_dev",
        *accessory_ids,
    ]
    for asset_id in ids:
        _validate_asset(asset_id, catalogs)
    candidate_id = f"pet_{seed[:20]}"
    return {
        "specSchemaVersion": "1",
        "candidateId": candidate_id,
        "profileRevision": profile["revision"],
        "mappingRuleVersion": catalogs.mapping_version,
        "assetCatalogVersion": catalogs.asset_catalog_version,
        "seed": seed,
        "speciesId": species_id,
        "bodyPaletteId": palette_id,
        "auraId": "aura_glow_dev",
        "eyeShapeId": "eyes_round_dev",
        "accessoryIds": accessory_ids,
        "idleMotionId": motion_id,
        "clickReactionSetId": "reaction_spark_dev",
        "copyRef": f"pet-copy:{candidate_id}",
        "traitBindings": [
            {
                "traitId": "bodyPaletteId",
                "derivedFrom": [f"{primary}:{top[0]['percent'] if top else 0}"],
            },
            {
                "traitId": "accessoryIds",
                "derivedFrom": [f"{secondary}:{top[1]['percent'] if len(top) > 1 else 0}"],
            },
        ],
        "customized": customized,
        "developmentOnly": not catalogs.production_eligible,
    }


def _template_copy(
    spec: dict[str, Any],
    profile: dict[str, Any],
    *,
    development_only: bool,
) -> dict[str, Any]:
    top = next((item for item in profile["percentages"] if item["moodId"] != "UNKNOWN"), None)
    mood = top["moodId"] if top else "UNKNOWN"
    percent = top["percent"] if top else 0
    stage_label = "开发态" if development_only else ""
    return {
        "status": "ok",
        "task_type": "pet_copy_generation",
        "petName": ["小音", "旋旋", "灵拍"][int(spec["seed"][:2], 16) % 3],
        "intro": f"这份歌单以 {mood} 氛围为主（{percent}%），生成了这只{stage_label}音灵。",
        "clickLines": ["一起听见今天的节奏。", "歌单的光正在闪动。", "需要时，我会安静待着。"],
        "warnings": ["TEMPLATE_FALLBACK", *(["DEVELOPMENT_ASSET"] if development_only else [])],
        "promptVersion": "fixed-template@1.0.0",
    }


def generate_copy(
    spec: dict[str, Any],
    profile: dict[str, Any],
    gateway: ModelGateway,
    settings: Settings,
    *,
    model_idempotency_key: str | None = None,
) -> tuple[dict[str, Any], ModelCallResult | None]:
    payload = {
        "task_type": "pet_copy_generation",
        "locale": "zh-CN",
        "moodProfile": {
            "percentages": {item["moodId"]: item["percent"] for item in profile["percentages"]},
            "unknownRate": profile["unknownCount"] / max(1, profile["normalizedTotal"] - profile["excludedCount"]),
        },
        "petSpec": {"candidateId": spec["candidateId"], "traitBindings": spec["traitBindings"]},
        "limits": {"nameChars": 8, "introChars": 60, "lineChars": 24, "lineCount": 5},
    }
    result = gateway.generate_copy(payload, idempotency_key=model_idempotency_key)
    if isinstance(result.output, PetCopyOutput) and result.output.status == "ok":
        candidate = result.output.model_dump(by_alias=True)
        strings = [candidate["petName"], candidate["intro"], *candidate["clickLines"]]
        if not any(term in text for term in FORBIDDEN_COPY for text in strings):
            return candidate, result
    return _template_copy(spec, profile, development_only=not settings.app_env == "production"), result


def _embed_layer(
    asset_id: str,
    catalogs: CatalogBundle,
    settings: Settings,
    replacements: dict[str, str] | None = None,
) -> str | None:
    asset = _validate_asset(asset_id, catalogs)
    uri = asset["uri"]
    if uri.startswith("inline://"):
        return None
    path = (settings.assets_path / uri).resolve()
    if sha256_bytes(path.read_bytes()) != asset["hash"]:
        raise ValueError("ASSET_HASH_MISMATCH")
    raw = path.read_text(encoding="utf-8")
    for token, value in (replacements or {}).items():
        raw = raw.replace(token, value)
    return base64.b64encode(raw.encode("utf-8")).decode()


def _wrap_chars(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for piece in str(text).split("\n"):
        current = ""
        for char in piece:
            if len(current) >= width:
                lines.append(current)
                current = char
            else:
                current += char
        if current:
            lines.append(current)
    return lines


def render_character_card(
    spec: dict[str, Any],
    copy: dict[str, Any],
    dna: dict[str, Any],
    catalogs: CatalogBundle,
    settings: Settings,
    pet_image: bytes | None = None,
) -> str:
    """Render a shareable playlist-pet character card as SVG (PRD F06)."""
    colors = catalogs.mapping["paletteColors"][spec["bodyPaletteId"]]
    body, accent = colors[0], colors[1]
    name = html.escape(str(copy.get("petName", "")))
    species_name = html.escape(SPECIES_DISPLAY_NAMES.get(spec["speciesId"], spec["speciesId"]))
    motion_name = html.escape(
        MOTION_DISPLAY_NAMES.get(
            spec["idleMotionId"],
            spec["idleMotionId"].replace("motion_", "").replace("_dev", ""),
        )
    )
    if pet_image:
        pet_layer = (
            f'<image x="180" y="140" width="360" height="360" preserveAspectRatio="xMidYMid meet" '
            f'href="data:image/png;base64,{base64.b64encode(pet_image).decode()}"/>'
        )
    else:
        species_b64 = _embed_layer(
            spec["speciesId"],
            catalogs,
            settings,
            replacements={"__BODY__": body, "__ACCENT__": accent},
        )
        accessory_id = spec["accessoryIds"][0] if spec["accessoryIds"] else None
        accessory_b64 = _embed_layer(accessory_id, catalogs, settings) if accessory_id else None
        accessory_layer = (
            f'<image x="180" y="140" width="360" height="360" href="data:image/svg+xml;base64,{accessory_b64}"/>'
            if accessory_b64
            else ""
        )
        pet_layer = (
            f'<image x="180" y="140" width="360" height="360" href="data:image/svg+xml;base64,{species_b64}"/>'
            f'{accessory_layer}'
        )
    summary = html.escape(str(dna.get("summary", "")))
    energy = max(0, min(100, int(dna.get("energy", 50))))
    genres = " / ".join(html.escape(item) for item in dna.get("genres", [])[:3])
    keywords = " · ".join(html.escape(item) for item in dna.get("keywords", [])[:4])
    tracks = dna.get("representativeTracks", [])[:4]
    tracks_lines = _wrap_chars(
        "\n".join(
            f"《{html.escape(item.get('title', ''))}》 {html.escape(item.get('artist', ''))}"
            for item in tracks
        ),
        26,
    )[:4]
    summary_lines = _wrap_chars(summary, 18)[:2]

    summary_svg = "".join(
        f'<text x="360" y="{565 + index * 34}" text-anchor="middle" font-family="sans-serif" '
        f'font-size="22" fill="#F7F7FB">{line}</text>'
        for index, line in enumerate(summary_lines)
    )
    tracks_svg = "".join(
        f'<text x="150" y="{862 + index * 32}" font-family="sans-serif" font-size="18" '
        f'fill="#A8ACC4">{line}</text>'
        for index, line in enumerate(tracks_lines)
    )
    bar_width = 480
    fill_width = round(bar_width * energy / 100)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1000" viewBox="0 0 720 1000">
<defs>
<radialGradient id="bg"><stop stop-color="{accent}"/><stop offset="1" stop-color="{body}"/></radialGradient>
<radialGradient id="glow"><stop stop-color="{accent}" stop-opacity="0.5"/><stop offset="1"
 stop-color="{accent}" stop-opacity="0"/></radialGradient>
</defs>
<rect width="720" height="1000" fill="#0E1021"/>
<rect x="24" y="24" width="672" height="952" rx="32" fill="#181B32" stroke="#2B2F4A" stroke-width="2"/>
<text x="360" y="78" text-anchor="middle" font-family="sans-serif" font-size="16" font-weight="700"
 letter-spacing="6" fill="#8B7CFF">PLAYLIST PET · 歌单角色卡</text>
<circle cx="360" cy="280" r="170" fill="url(#bg)" opacity="0.16"/>
<circle cx="360" cy="280" r="150" fill="url(#glow)"/>
{pet_layer}
<text x="360" y="520" text-anchor="middle" font-family="sans-serif" font-size="44" font-weight="800" fill="{body}">{name}</text>
<text x="360" y="556" text-anchor="middle" font-family="sans-serif" font-size="18"
 fill="#A8ACC4">{species_name} · {motion_name}动作</text>
{summary_svg}
<text x="120" y="650" font-family="sans-serif" font-size="16" font-weight="700" fill="#F7F7FB">能量值</text>
<text x="600" y="650" text-anchor="end" font-family="sans-serif" font-size="16" fill="#A8ACC4">{energy}/100</text>
<rect x="120" y="662" width="480" height="14" rx="7" fill="#1E2138"/>
<rect x="120" y="662" width="{fill_width}" height="14" rx="7" fill="{accent}"/>
<text x="120" y="718" font-family="sans-serif" font-size="16" font-weight="700" fill="#F7F7FB">主流派</text>
<text x="120" y="742" font-family="sans-serif" font-size="18" fill="#A8ACC4">{genres}</text>
<text x="120" y="788" font-family="sans-serif" font-size="16" font-weight="700" fill="#F7F7FB">审美关键词</text>
<text x="120" y="812" font-family="sans-serif" font-size="18" fill="#A8ACC4">{keywords}</text>
<text x="120" y="852" font-family="sans-serif" font-size="16" font-weight="700" fill="#F7F7FB">代表歌曲</text>
{tracks_svg}
<circle cx="340" cy="920" r="14" fill="{body}"/>
<circle cx="372" cy="920" r="14" fill="{accent}"/>
<text x="402" y="926" font-family="sans-serif" font-size="16" fill="#A8ACC4">{species_name} · 主色</text>
<text x="360" y="960" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#A8ACC4">由歌单生成 · MeloPet</text>
</svg>"""
    return svg


def render_preview(spec: dict[str, Any], copy: dict[str, Any], catalogs: CatalogBundle, settings: Settings) -> str:
    colors = catalogs.mapping["paletteColors"][spec["bodyPaletteId"]]
    body, accent = colors[0], colors[1]
    species_b64 = _embed_layer(
        spec["speciesId"],
        catalogs,
        settings,
        replacements={"__BODY__": body, "__ACCENT__": accent},
    )
    accessory_id = spec["accessoryIds"][0] if spec["accessoryIds"] else None
    accessory_b64 = _embed_layer(accessory_id, catalogs, settings) if accessory_id else None
    name = html.escape(copy["petName"])
    watermark = (
        '<text x="344" y="24" text-anchor="end" font-family="sans-serif" font-size="11" '
        'fill="#64748b">DEV ONLY</text>'
        if spec["developmentOnly"]
        else ""
    )
    accessory_layer = (
        f'<image x="70" y="45" width="220" height="220" href="data:image/svg+xml;base64,{accessory_b64}"/>'
        if accessory_b64
        else ""
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="360" height="360" viewBox="0 0 360 360">
<defs>
<radialGradient id="bg"><stop stop-color="{accent}"/><stop offset="1" stop-color="{body}"/></radialGradient>
<radialGradient id="glow"><stop stop-color="{accent}" stop-opacity="0.5"/><stop offset="1" stop-color="{accent}"
 stop-opacity="0"/></radialGradient>
</defs>
<rect width="360" height="360" rx="36" fill="url(#bg)" opacity="0.22"/>
<circle cx="180" cy="170" r="150" fill="url(#glow)"/>
<circle cx="180" cy="170" r="126" fill="none" stroke="{accent}" stroke-opacity="0.5" stroke-width="3" stroke-dasharray="4 10"/>
<g fill="{accent}" opacity="0.9">
<circle cx="56" cy="84" r="5"/><circle cx="298" cy="118" r="4"/><circle cx="84" cy="256" r="6"/>
<circle cx="274" cy="272" r="5"/><circle cx="150" cy="44" r="4"/><circle cx="240" cy="302" r="5"/>
</g>
<image x="70" y="45" width="220" height="220" href="data:image/svg+xml;base64,{species_b64}"/>
{accessory_layer}
<text x="180" y="330" text-anchor="middle" font-family="sans-serif" font-size="26" font-weight="700" fill="{body}">{name}</text>
{watermark}
</svg>""".encode()
    store = LocalObjectStore(settings.preview_path)
    return store.put(svg, suffix=".svg")


PET_ACTIONS = ("idle", "listen", "dance", "sleep")

ACTION_PROMPTS = {
    "idle": "待机动作：轻轻呼吸、眨眼、尾巴轻摆",
    "listen": "听歌动作：戴着耳机，随着音乐轻轻点头",
    "dance": "跳舞动作：跟随音乐欢快摇摆",
    "sleep": "睡觉动作：趴下安静睡觉、闭眼",
}


def build_image_prompt(
    spec: dict[str, Any],
    copy: dict[str, Any],
    profile: dict[str, Any],
    catalogs: CatalogBundle,
) -> str:
    """Build the AI image prompt for a pet following the Playlist Pet UI spec.

    The spec requires: Q版 + 轻二次元 + 柔和插画/Pixel 可选、3~5 个核心色、
    最多 2 个配饰、头身比 1:1.5~1:2、表情明显、透明/干净背景。颜色来自
    情绪配色模板，不由模型自由发挥。
    """
    top = next((item for item in profile.get("percentages", []) if item.get("moodId") != "UNKNOWN"), None)
    mood_name = catalogs.taxonomy_by_id.get(top["moodId"], {}).get("displayName", "治愈") if top else "治愈"
    species = SPECIES_DISPLAY_NAMES.get(spec["speciesId"], "小动物")
    colors = catalogs.mapping["paletteColors"][spec["bodyPaletteId"]]
    accessories = [ACCESSORY_DISPLAY_NAMES.get(aid, aid) for aid in spec.get("accessoryIds", [])]
    accessory_text = "、".join(accessories) if accessories else "无"
    motion = MOTION_DISPLAY_NAMES.get(spec["idleMotionId"], "待机")
    name = copy.get("petName", "小宠")
    intro = copy.get("intro", "")
    return (
        f"一只Q版二次元桌面宠物（chibi desktop pet），物种：{species}，名字：{name}。"
        f"视觉风格：柔和插画、轻二次元、轮廓清晰、表情明显可爱，头身比约1:1.5到1:2。"
        f"核心配色（最多3~5色）：主色{colors[0]}、辅色{colors[1]}。"
        f"配饰（最多2个）：{accessory_text}。"
        f"音乐人格：{mood_name}，待机动作：{motion}。"
        f"人物设定：{intro}。"
        f"全身像，正面或3/4视角，干净纯色浅色背景，缩小到桌面图标仍可辨识。"
    )


def build_action_prompt(
    spec: dict[str, Any],
    copy: dict[str, Any],
    profile: dict[str, Any],
    catalogs: CatalogBundle,
    action: str,
) -> str:
    """Build a prompt for one of the four MVP pet actions (PRD 第 13 节)."""
    base = build_image_prompt(spec, copy, profile, catalogs)
    return f"{base} {ACTION_PROMPTS.get(action, '')}"


def create_candidate(
    *,
    profile: dict[str, Any],
    playlist_hash: str,
    candidate_index: int,
    catalogs: CatalogBundle,
    gateway: ModelGateway,
    settings: Settings,
    model_idempotency_key: str | None = None,
    customization: PetCustomization | None = None,
) -> tuple[PetCandidateResult, ModelCallResult | None]:
    spec = map_pet_spec(
        profile=profile,
        playlist_hash=playlist_hash,
        candidate_index=candidate_index,
        catalogs=catalogs,
        customization=customization,
    )
    copy, model_call = generate_copy(
        spec,
        profile,
        gateway,
        settings,
        model_idempotency_key=model_idempotency_key,
    )
    if spec["developmentOnly"]:
        # 开发素材：候选预览统一使用确定性 SVG，保证网页预览与桌面宠物（desktop-pet/pet.py）外观一致。
        preview_ref = render_preview(spec, copy, catalogs, settings)
    else:
        image = gateway.generate_image(
            build_image_prompt(spec, copy, profile, catalogs),
            idempotency_key=model_idempotency_key,
        )
        if image.image_bytes:
            store = LocalObjectStore(settings.preview_path)
            preview_ref = store.put(image.image_bytes, suffix=".png")
        else:
            preview_ref = render_preview(spec, copy, catalogs, settings)
    return PetCandidateResult(spec["candidateId"], spec, copy, preview_ref), model_call
