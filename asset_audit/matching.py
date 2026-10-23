from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

ASSET_ID_THRESHOLD = 1_000_000_000_000

Asset = Mapping[str, Any]


@dataclass(frozen=True)
class ResolvedLocation:
    location_id: int
    name: str
    solar_system_id: int
    solar_system_name: str


def _resolve_location(
    asset: Asset,
    assets_by_item_id: Mapping[int, Asset],
    locations_by_id: Mapping[int, ResolvedLocation],
    visited_item_ids: set[int],
) -> Optional[ResolvedLocation]:
    location_type = str(asset.get("location_type", ""))
    location_id = int(asset["location_id"])

    if location_type == "solar_system":
        return locations_by_id.get(location_id)

    parent_asset = assets_by_item_id.get(location_id)
    if location_id <= ASSET_ID_THRESHOLD or parent_asset is None:
        return locations_by_id.get(location_id)
    if location_id in visited_item_ids:
        return None

    visited_item_ids.add(location_id)
    return _resolve_location(
        parent_asset,
        assets_by_item_id,
        locations_by_id,
        visited_item_ids,
    )


def matching_assets(
    assets: Iterable[Asset],
    target_type_ids: Iterable[int],
    target_solar_system_ids: Iterable[int],
    locations_by_id: Mapping[int, ResolvedLocation],
) -> list[Asset]:
    """Return assets matching both type and solar-system filters."""
    assets = list(assets)
    target_type_ids = set(target_type_ids)
    target_solar_system_ids = set(target_solar_system_ids)
    if not target_type_ids or not target_solar_system_ids:
        return []

    assets_by_item_id = {int(asset["item_id"]): asset for asset in assets}

    matches = []
    for asset in assets:
        if int(asset["type_id"]) not in target_type_ids:
            continue

        location = _resolve_location(
            asset,
            assets_by_item_id,
            locations_by_id,
            set(),
        )
        if location and location.solar_system_id in target_solar_system_ids:
            matches.append(
                {
                    **asset,
                    "resolved_location_id": location.location_id,
                    "resolved_location_name": location.name,
                    "resolved_solar_system_id": location.solar_system_id,
                    "resolved_solar_system_name": location.solar_system_name,
                }
            )

    return matches
