from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .util import sha256_bytes


@dataclass(frozen=True)
class CatalogBundle:
    taxonomy_version: str
    taxonomy: list[dict[str, Any]]
    taxonomy_by_id: dict[str, dict[str, Any]]
    taxonomy_order: list[str]
    mapping_version: str
    mapping: dict[str, Any]
    asset_catalog_version: str
    assets: dict[str, dict[str, Any]]
    production_eligible: bool


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalogs(settings: Settings) -> CatalogBundle:
    taxonomy_doc = _read_json(settings.catalogs_path / f"mood-taxonomy.v{settings.taxonomy_version}.json")
    mapping_doc = _read_json(settings.catalogs_path / f"mapping-rules.v{settings.mapping_rule_version}.json")
    asset_doc = _read_json(settings.catalogs_path / f"asset-catalog.{settings.asset_catalog_version}.json")
    if taxonomy_doc["version"] != settings.taxonomy_version:
        raise ValueError("taxonomy version mismatch")
    if mapping_doc["version"] != settings.mapping_rule_version:
        raise ValueError("mapping rule version mismatch")
    if asset_doc["version"] != settings.asset_catalog_version:
        raise ValueError("asset catalog version mismatch")

    taxonomy = taxonomy_doc["taxonomy"]
    taxonomy_by_id = {item["moodId"]: item for item in taxonomy}
    if "UNKNOWN" not in taxonomy_by_id or len(taxonomy_by_id) != len(taxonomy):
        raise ValueError("taxonomy must contain unique IDs and UNKNOWN")
    assets = {item["assetId"]: item for item in asset_doc["assets"]}
    if len(assets) != len(asset_doc["assets"]):
        raise ValueError("asset IDs must be unique")

    for asset in assets.values():
        if not asset["enabled"]:
            continue
        uri = asset["uri"]
        if uri.startswith("inline://"):
            continue
        path = (settings.assets_path / uri).resolve()
        if settings.assets_path.resolve() not in path.parents:
            raise ValueError(f"asset path escapes allowlist: {asset['assetId']}")
        if not path.is_file() or sha256_bytes(path.read_bytes()) != asset["hash"]:
            raise ValueError(f"asset missing or hash mismatch: {asset['assetId']}")

    return CatalogBundle(
        taxonomy_version=taxonomy_doc["version"],
        taxonomy=taxonomy,
        taxonomy_by_id=taxonomy_by_id,
        taxonomy_order=[item["moodId"] for item in taxonomy],
        mapping_version=mapping_doc["version"],
        mapping=mapping_doc,
        asset_catalog_version=asset_doc["version"],
        assets=assets,
        production_eligible=bool(asset_doc.get("productionEligible", False)),
    )
