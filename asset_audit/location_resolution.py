import logging
from collections.abc import Callable, Iterable
from typing import Any

from asset_audit.esi import (
    STRUCTURE_READ_SCOPE,
    _lookup_token,
    fetch_station_details,
    fetch_structure_details,
)
from asset_audit.matching import ASSET_ID_THRESHOLD, ResolvedLocation
from asset_audit.models import (
    SolarSystemReference,
    StationReference,
    StructureReference,
)

logger = logging.getLogger(__name__)

STATION_ID_MIN = 60_000_000
STATION_ID_MAX = 69_999_999


def terminal_location_ids(assets: Iterable[dict[str, Any]]) -> set[int]:
    assets = list(assets)
    assets_by_item_id = {
        int(asset["item_id"]): asset
        for asset in assets
        if int(asset["item_id"]) > ASSET_ID_THRESHOLD
    }
    terminal_ids = set()

    for asset in assets:
        current_asset = asset
        visited_item_ids = set()
        while True:
            location_id = int(current_asset["location_id"])
            if current_asset.get("location_type") == "solar_system":
                terminal_ids.add(location_id)
                break
            if location_id <= ASSET_ID_THRESHOLD:
                terminal_ids.add(location_id)
                break
            if location_id not in assets_by_item_id:
                terminal_ids.add(location_id)
                break
            if location_id in visited_item_ids:
                break

            visited_item_ids.add(location_id)
            current_asset = assets_by_item_id[location_id]

    return terminal_ids


def resolve_asset_locations(
    assets: Iterable[dict[str, Any]],
    character_id: int,
    owner_hash: str,
    *,
    token_lookup: Callable[[int, list[str], str | None], Any] | None = None,
    structure_token: Any = None,
    esi_provider: Any = None,
) -> tuple[dict[int, ResolvedLocation], set[int]]:
    terminal_ids = terminal_location_ids(assets)
    return resolve_location_ids(
        terminal_ids,
        character_id,
        owner_hash,
        token_lookup=token_lookup,
        structure_token=structure_token,
        esi_provider=esi_provider,
    )


def resolve_location_ids(
    location_ids: Iterable[int],
    character_id: int,
    owner_hash: str,
    *,
    token_lookup: Callable[[int, list[str], str | None], Any] | None = None,
    structure_token: Any = None,
    esi_provider: Any = None,
) -> tuple[dict[int, ResolvedLocation], set[int]]:
    terminal_ids = {int(location_id) for location_id in location_ids}
    system_names = dict(
        SolarSystemReference.objects.filter(pk__in=terminal_ids).values_list(
            "id", "name"
        )
    )

    station_ids = {
        location_id
        for location_id in terminal_ids
        if STATION_ID_MIN <= location_id <= STATION_ID_MAX
    }
    cached_station_ids = set(
        StationReference.objects.filter(station_id__in=station_ids).values_list(
            "station_id", flat=True
        )
    )
    for station_id in sorted(station_ids - cached_station_ids):
        try:
            station = fetch_station_details(
                station_id,
                esi_provider=esi_provider,
            )
            StationReference.objects.update_or_create(
                station_id=station_id,
                defaults={
                    "name": station["name"],
                    "solar_system_id": station["system_id"],
                },
            )
        except Exception:
            logger.warning(
                "Could not resolve station location %s for character %s.",
                station_id,
                character_id,
                exc_info=True,
            )

    structure_ids = {
        location_id
        for location_id in terminal_ids
        if location_id > ASSET_ID_THRESHOLD
    }
    cached_structure_ids = set(
        StructureReference.objects.filter(structure_id__in=structure_ids).values_list(
            "structure_id", flat=True
        )
    )
    unresolved_structure_ids = structure_ids - cached_structure_ids
    if unresolved_structure_ids:
        if structure_token is None:
            token_lookup = token_lookup or _lookup_token
            try:
                structure_token = token_lookup(
                    character_id,
                    [STRUCTURE_READ_SCOPE],
                    owner_hash,
                )
            except Exception:
                structure_token = None
                logger.warning(
                    "Could not find a structure-scoped token for character %s.",
                    character_id,
                    exc_info=True,
                )

        if structure_token is None:
            logger.warning(
                "Character %s cannot resolve %d structure location(s) without "
                "a structure-scoped token.",
                character_id,
                len(unresolved_structure_ids),
            )
        else:
            for structure_id in sorted(unresolved_structure_ids):
                try:
                    structure = fetch_structure_details(
                        structure_id,
                        structure_token,
                        esi_provider=esi_provider,
                    )
                    StructureReference.objects.update_or_create(
                        structure_id=structure_id,
                        defaults={
                            "name": structure["name"],
                            "solar_system_id": structure["solar_system_id"],
                        },
                    )
                except Exception:
                    logger.warning(
                        "Could not resolve structure location %s for character %s.",
                        structure_id,
                        character_id,
                        exc_info=True,
                    )

    stations = list(
        StationReference.objects.filter(station_id__in=station_ids).values(
            "station_id", "name", "solar_system_id"
        )
    )
    structures = list(
        StructureReference.objects.filter(structure_id__in=structure_ids).values(
            "structure_id", "name", "solar_system_id"
        )
    )
    all_system_ids = set(system_names)
    all_system_ids.update(station["solar_system_id"] for station in stations)
    all_system_ids.update(structure["solar_system_id"] for structure in structures)
    system_names.update(
        SolarSystemReference.objects.filter(pk__in=all_system_ids).values_list(
            "id", "name"
        )
    )

    locations = {
        system_id: ResolvedLocation(
            location_id=system_id,
            name=name,
            solar_system_id=system_id,
            solar_system_name=name,
        )
        for system_id, name in system_names.items()
    }
    for station in stations:
        system_id = station["solar_system_id"]
        locations[station["station_id"]] = ResolvedLocation(
            location_id=station["station_id"],
            name=station["name"],
            solar_system_id=system_id,
            solar_system_name=system_names.get(
                system_id, f"Unknown system ({system_id})"
            ),
        )
    for structure in structures:
        system_id = structure["solar_system_id"]
        locations[structure["structure_id"]] = ResolvedLocation(
            location_id=structure["structure_id"],
            name=structure["name"],
            solar_system_id=system_id,
            solar_system_name=system_names.get(
                system_id, f"Unknown system ({system_id})"
            ),
        )

    unresolved_ids = terminal_ids - set(locations)
    return locations, unresolved_ids