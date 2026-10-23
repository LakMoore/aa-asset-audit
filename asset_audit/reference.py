from django.apps import apps
from django.db.utils import OperationalError, ProgrammingError

from asset_audit.models import (
    InventoryTypeReference,
    MarketGroupReference,
    SolarSystemReference,
)
from asset_audit.reference_data import (
    ReferenceDataUnavailable,
    ensure_reference_dataset_loaded,
)


class ReferenceDataUnavailableError(Exception):
    """Raised when local reference data is not ready for search."""


class ReferenceNameLookupError(Exception):
    """Raised when cached reference names are not available for lookup."""


def _search_dataset(
    dataset: str,
    model,
    query: str,
    fields: tuple[str, ...],
    limit: int,
) -> list[dict[str, int | str | None]]:
    if not apps.is_installed("asset_audit"):
        raise ReferenceDataUnavailableError
    try:
        ensure_reference_dataset_loaded(dataset)
        results = list(
            model.objects.filter(name__icontains=query)
            .order_by("name", "id")
            .values(*fields)[:limit]
        )
        if not results and not model.objects.exists():
            raise ReferenceDataUnavailableError
    except (
        OperationalError,
        ProgrammingError,
        ReferenceDataUnavailable,
    ) as error:
        raise ReferenceDataUnavailableError from error
    return results


def search_solar_systems(
    query: str, limit: int = 20
) -> list[dict[str, int | str | None]]:
    return _search_dataset(
        "solar_systems", SolarSystemReference, query, ("id", "name"), limit
    )


def lookup_solar_system_ids(names: list[str]) -> dict[str, list[int]]:
    try:
        ensure_reference_dataset_loaded("solar_systems")
        resolved = {}
        for name in names:
            system_ids = list(
                SolarSystemReference.objects.filter(name__iexact=name)
                .order_by("id")
                .values_list("id", flat=True)
            )
            if system_ids:
                resolved[name.casefold()] = system_ids
        return resolved
    except (OperationalError, ProgrammingError, ReferenceDataUnavailable) as error:
        raise ReferenceNameLookupError from error


def lookup_inventory_type_ids(names: list[str]) -> dict[str, list[int]]:
    try:
        ensure_reference_dataset_loaded("item_types")
        resolved = {}
        for name in names:
            type_ids = list(
                InventoryTypeReference.objects.filter(name__iexact=name)
                .order_by("id")
                .values_list("id", flat=True)
            )
            if type_ids:
                resolved[name.casefold()] = type_ids
        return resolved
    except (OperationalError, ProgrammingError, ReferenceDataUnavailable) as error:
        raise ReferenceNameLookupError from error


def lookup_market_group_ids(names: list[str]) -> dict[str, list[int]]:
    try:
        ensure_reference_dataset_loaded("market_groups")
        group_rows = list(
            MarketGroupReference.objects.values_list("id", "name", "parent_group_id")
        )
        labels_by_id = _market_group_labels(group_rows)
        ids_by_label = {
            label.casefold(): group_id for group_id, label in labels_by_id.items()
        }
        groups_by_id, paths = _market_group_parent_paths(group_rows)
        ids_by_name = {}
        for group_id, (group_name, _) in groups_by_id.items():
            ids_by_name.setdefault(group_name.casefold(), []).append(group_id)
        resolved = {}
        for name in names:
            selected_id = ids_by_label.get(name.casefold())
            if selected_id is not None:
                resolved[name.casefold()] = [selected_id]
                continue
            base_name, separator, qualifier = name.partition(" (")
            if separator and qualifier.endswith(")"):
                legacy_parent_path = qualifier[:-1].casefold()
                legacy_matches = [
                    group_id
                    for group_id in ids_by_name.get(base_name.casefold(), [])
                    if any(
                        " / ".join(paths[group_id][:depth]).casefold()
                        == legacy_parent_path
                        for depth in range(1, len(paths[group_id]) + 1)
                    )
                ]
                if len(legacy_matches) == 1:
                    resolved[name.casefold()] = legacy_matches
                    continue
            group_ids = list(
                MarketGroupReference.objects.filter(name__iexact=name)
                .order_by("id")
                .values_list("id", flat=True)
            )
            if group_ids:
                resolved[name.casefold()] = group_ids
        return resolved
    except (OperationalError, ProgrammingError, ReferenceDataUnavailable) as error:
        raise ReferenceNameLookupError from error


def market_group_display_names(group_ids: list[int]) -> dict[int, str]:
    try:
        group_rows = list(
            MarketGroupReference.objects.values_list("id", "name", "parent_group_id")
        )
        labels_by_id = _market_group_labels(group_rows)
        return {
            int(group_id): labels_by_id[int(group_id)]
            for group_id in group_ids
            if int(group_id) in labels_by_id
        }
    except (OperationalError, ProgrammingError, ReferenceDataUnavailable) as error:
        raise ReferenceNameLookupError from error


def _market_group_labels(
    groups: list[tuple[int, str, int | None]],
) -> dict[int, str]:
    groups_by_id, paths = _market_group_parent_paths(groups)
    ids_by_name = {}
    for group_id, (name, _) in groups_by_id.items():
        ids_by_name.setdefault(name.casefold(), []).append(group_id)

    labels = {}
    for group_id, (name, _) in groups_by_id.items():
        duplicate_ids = ids_by_name[name.casefold()]
        if len(duplicate_ids) == 1:
            labels[group_id] = name
            continue

        path = paths[group_id]
        ancestor_groups = {}
        for other_id in duplicate_ids:
            for ancestor_name in set(paths[other_id]):
                ancestor_groups.setdefault(ancestor_name.casefold(), set()).add(
                    other_id
                )
        unique_parent_names = [
            ancestor_name
            for ancestor_name in path
            if ancestor_groups[ancestor_name.casefold()] == {group_id}
        ]
        parent_label = unique_parent_names[0] if unique_parent_names else (
            path[0] if path else f"group {group_id}"
        )
        if parent_label.casefold() == "blueprints & reactions":
            parent_label = "Blueprints"
        labels[group_id] = f"{name} ({parent_label})"

    ids_by_label = {}
    for group_id, label in labels.items():
        ids_by_label.setdefault(label.casefold(), []).append(group_id)
    for duplicate_ids in ids_by_label.values():
        if len(duplicate_ids) > 1:
            for group_id in duplicate_ids:
                labels[group_id] = f"{labels[group_id]} [group {group_id}]"
    return labels


def _market_group_parent_paths(
    groups: list[tuple[int, str, int | None]],
) -> tuple[dict[int, tuple[str, int | None]], dict[int, list[str]]]:
    groups_by_id = {
        int(group_id): (name, parent_id)
        for group_id, name, parent_id in groups
    }

    def parent_path(group_id: int) -> list[str]:
        path = []
        visited = {group_id}
        parent_id = groups_by_id[group_id][1]
        while parent_id is not None and int(parent_id) not in visited:
            parent_id = int(parent_id)
            visited.add(parent_id)
            parent = groups_by_id.get(parent_id)
            if parent is None:
                path.append(f"Market group {parent_id}")
                break
            path.append(parent[0])
            parent_id = parent[1]
        return path

    paths = {
        group_id: parent_path(group_id)
        for group_id in groups_by_id
    }
    return groups_by_id, paths


def lookup_inventory_type_ids_for_market_groups(
    market_group_ids: list[int],
) -> list[int]:
    if not market_group_ids:
        return []

    children_by_parent = {}
    for group_id, parent_group_id in MarketGroupReference.objects.values_list(
        "id", "parent_group_id"
    ):
        if parent_group_id is not None:
            children_by_parent.setdefault(parent_group_id, []).append(group_id)

    included_group_ids = set()
    pending_group_ids = list(market_group_ids)
    while pending_group_ids:
        group_id = pending_group_ids.pop()
        if group_id in included_group_ids:
            continue
        included_group_ids.add(group_id)
        pending_group_ids.extend(children_by_parent.get(group_id, []))

    return list(
        InventoryTypeReference.objects.filter(
            market_group_id__in=included_group_ids
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def search_inventory_types(
    query: str, limit: int = 20
) -> list[dict[str, int | str | None]]:
    return _search_dataset(
        "item_types",
        InventoryTypeReference,
        query,
        ("id", "name", "market_group_id"),
        limit,
    )


def search_market_groups(
    query: str, limit: int = 20
) -> list[dict[str, int | str | None]]:
    results = _search_dataset(
        "market_groups",
        MarketGroupReference,
        query,
        ("id", "name", "parent_group_id"),
        limit,
    )
    labels_by_id = market_group_display_names(
        [int(result["id"]) for result in results]
    )
    return [
        {**result, "label": labels_by_id.get(int(result["id"]), result["name"])}
        for result in results
    ]