from django.contrib import messages
import hashlib
import json
import logging

from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from asset_audit.esi import (
    ASSET_READ_SCOPE,
    CHARACTER_CONTRACTS_READ_SCOPE,
    CHARACTER_ORDERS_READ_SCOPE,
    CHARACTER_ROLES_SCOPE,
    CORPORATION_ASSET_READ_SCOPE,
    CORPORATION_CONTRACTS_READ_SCOPE,
    CORPORATION_ORDERS_READ_SCOPE,
)
from asset_audit.forms import AssetAuditForm
from asset_audit.matching import ResolvedLocation, matching_assets
from asset_audit.models import (
    CharacterAssetSnapshot,
    CharacterContractSnapshot,
    CharacterOrderSnapshot,
    CharacterRoleSnapshot,
    CorporationAssetSnapshot,
    CorporationContractSnapshot,
    CorporationOrderSnapshot,
    ContractItemSnapshot,
    InventoryTypeReference,
    RecentAssetAuditSearch,
    SolarSystemReference,
)
from asset_audit.reference import (
    ReferenceDataUnavailableError,
    lookup_inventory_type_ids_for_market_groups,
    market_group_display_names,
    search_inventory_types,
    search_market_groups,
    search_solar_systems,
)
from asset_audit.snapshots import (
    AssetSnapshotAccessError,
    refresh_character_role_snapshot,
    refresh_character_snapshot,
    refresh_character_contract_items,
    refresh_character_contract_snapshot,
    refresh_character_order_snapshot,
    refresh_corporation_contract_items,
    refresh_corporation_contract_snapshot,
    refresh_corporation_order_snapshot,
    refresh_corporation_snapshot,
)

logger = logging.getLogger(__name__)


def _reference_search_response(request, search):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    try:
        results = search(query)
    except ReferenceDataUnavailableError:
        return JsonResponse(
            {
                "results": [],
                "error": "Reference data is not loaded on this server.",
            },
            status=503,
        )
    return JsonResponse({"results": results})


@permission_required("asset_audit.run_alliance_asset_audit")
@require_GET
def solar_system_reference(request):
    return _reference_search_response(request, search_solar_systems)


@permission_required("asset_audit.run_alliance_asset_audit")
@require_GET
def item_type_reference(request):
    return _reference_search_response(request, search_inventory_types)


@permission_required("asset_audit.run_alliance_asset_audit")
@require_GET
def market_group_reference(request):
    return _reference_search_response(request, search_market_groups)


def _cache_status(
    expires_at,
    has_scope: bool,
    now,
    *,
    fetched_at=None,
    not_applicable: bool = False,
    not_applicable_label: str = "Not applicable",
):
    if not has_scope:
        return {
            "expires_at": expires_at,
            "freshness_class": "text-warning",
            "freshness_label": "Missing scope",
            "refreshable": False,
            "timer_active": False,
            "has_scope": False,
            "fetched_at": fetched_at,
        }
    if not_applicable:
        return {
            "expires_at": None,
            "freshness_class": "text-muted",
            "freshness_label": not_applicable_label,
            "refreshable": False,
            "timer_active": False,
            "has_scope": True,
            "fetched_at": fetched_at,
        }
    if expires_at is None or expires_at <= now:
        return {
            "expires_at": expires_at,
            "freshness_class": "text-danger",
            "freshness_label": "Missing expiry" if expires_at is None else "Expired",
            "refreshable": True,
            "timer_active": True,
            "has_scope": True,
            "fetched_at": fetched_at,
        }
    if fetched_at and fetched_at >= now - timezone.timedelta(minutes=5):
        return {
            "expires_at": expires_at,
            "freshness_class": "text-success",
            "freshness_label": "Fresh",
            "refreshable": False,
            "timer_active": True,
            "has_scope": True,
            "fetched_at": fetched_at,
        }
    if expires_at <= now + timezone.timedelta(minutes=45):
        return {
            "expires_at": expires_at,
            "freshness_class": "text-warning",
            "freshness_label": "Expiring soon",
            "refreshable": False,
            "timer_active": True,
            "has_scope": True,
            "fetched_at": fetched_at,
        }
    return {
        "expires_at": expires_at,
        "freshness_class": "text-success",
        "freshness_label": "Fresh",
        "refreshable": False,
        "timer_active": True,
        "has_scope": True,
        "fetched_at": fetched_at,
    }


def _endpoint_rows(statuses):
    labels = {
        "assets": "Assets",
        "contracts": "Contracts",
        "contract_items": "Contract items",
        "orders": "Market orders",
    }
    return [
        {"key": key, "label": labels[key], **status}
        for key, status in statuses.items()
    ]


def _contract_items_cache_status(
    contracts_snapshot,
    item_snapshots,
    owner_hash: str,
    has_scope: bool,
    now,
):
    if not has_scope:
        return _cache_status(None, False, now)
    if contracts_snapshot is None:
        return {
            **_cache_status(None, True, now, not_applicable=True),
            "freshness_label": "Waiting for contracts",
        }
    contract_ids = {
        int(contract["contract_id"])
        for contract in contracts_snapshot.contracts
        if contract.get("type") in {"item_exchange", "auction"}
    }
    if not contract_ids:
        return _cache_status(
            None,
            has_scope,
            now,
            not_applicable=True,
            not_applicable_label="No item contracts",
        )

    cached = {
        snapshot.contract_id: snapshot
        for snapshot in item_snapshots
        if snapshot.owner_hash == owner_hash
        and snapshot.contract_id in contract_ids
    }
    if contract_ids - set(cached):
        return _cache_status(None, has_scope, now)
    expiries = [cached[contract_id].expires_at for contract_id in contract_ids]
    if any(expires_at is None for expires_at in expiries):
        return _cache_status(None, has_scope, now)
    expires_at = min(expiries)
    return _cache_status(
        expires_at,
        has_scope,
        now,
        fetched_at=max(snapshot.fetched_at for snapshot in cached.values()),
    )


def get_character_statuses(ownership_model=None, token_model=None):
    if ownership_model is None or token_model is None:
        from allianceauth.authentication.models import CharacterOwnership
        from esi.models import Token

        ownership_model = ownership_model or CharacterOwnership
        token_model = token_model or Token

    owners = list(
        ownership_model.objects.filter(user__is_active=True).values_list(
            "character__character_id",
            "character__character_name",
            "character__corporation_id",
            "character__corporation_name",
            "owner_hash",
        )
    )
    if not owners:
        return []

    owner_pairs = {
        (int(character_id), owner_hash)
        for character_id, _, _, _, owner_hash in owners
    }
    token_filters = {
        "character_id__in": [character_id for character_id, _ in owner_pairs],
        "character_owner_hash__in": [owner_hash for _, owner_hash in owner_pairs],
    }

    def token_pairs_for_scope(scope):
        return set(
            token_model.objects.filter(**token_filters)
            .require_scopes([scope])
            .values_list("character_id", "character_owner_hash")
        )

    asset_token_pairs = token_pairs_for_scope(ASSET_READ_SCOPE)
    roles_token_pairs = token_pairs_for_scope(CHARACTER_ROLES_SCOPE)
    contract_token_pairs = token_pairs_for_scope(CHARACTER_CONTRACTS_READ_SCOPE)
    order_token_pairs = token_pairs_for_scope(CHARACTER_ORDERS_READ_SCOPE)
    corporation_asset_token_pairs = token_pairs_for_scope(
        CORPORATION_ASSET_READ_SCOPE
    )
    corporation_contract_token_pairs = token_pairs_for_scope(
        CORPORATION_CONTRACTS_READ_SCOPE
    )
    corporation_order_token_pairs = token_pairs_for_scope(
        CORPORATION_ORDERS_READ_SCOPE
    )
    snapshots = {
        snapshot.character_id: snapshot
        for snapshot in CharacterAssetSnapshot.objects.filter(
            character_id__in=[character_id for character_id, _ in owner_pairs]
        )
    }
    role_snapshots = {
        snapshot.character_id: snapshot
        for snapshot in CharacterRoleSnapshot.objects.filter(
            character_id__in=[character_id for character_id, _ in owner_pairs]
        )
    }
    contract_snapshots = {
        snapshot.character_id: snapshot
        for snapshot in CharacterContractSnapshot.objects.filter(
            character_id__in=[character_id for character_id, _ in owner_pairs]
        )
    }
    order_snapshots = {
        snapshot.character_id: snapshot
        for snapshot in CharacterOrderSnapshot.objects.filter(
            character_id__in=[character_id for character_id, _ in owner_pairs]
        )
    }
    contract_item_snapshots = {}
    for snapshot in ContractItemSnapshot.objects.filter(
        owner_type=ContractItemSnapshot.OwnerType.CHARACTER,
        owner_id__in=[character_id for character_id, _ in owner_pairs],
    ):
        contract_item_snapshots.setdefault(snapshot.owner_id, []).append(snapshot)
    now = timezone.now()
    amber_limit = now + timezone.timedelta(minutes=45)
    characters = []
    for (
        character_id,
        character_name,
        corporation_id,
        corporation_name,
        owner_hash,
    ) in owners:
        character_id = int(character_id)
        corporation_id = int(corporation_id) if corporation_id else None
        has_asset_scope = (character_id, owner_hash) in asset_token_pairs
        has_roles_scope = (character_id, owner_hash) in roles_token_pairs
        has_contract_scope = (character_id, owner_hash) in contract_token_pairs
        has_order_scope = (character_id, owner_hash) in order_token_pairs
        has_corporation_asset_scope = (
            character_id,
            owner_hash,
        ) in corporation_asset_token_pairs
        snapshot = snapshots.get(character_id)
        if snapshot and snapshot.owner_hash != owner_hash:
            snapshot = None
        contract_snapshot = contract_snapshots.get(character_id)
        if contract_snapshot and contract_snapshot.owner_hash != owner_hash:
            contract_snapshot = None
        order_snapshot = order_snapshots.get(character_id)
        if order_snapshot and order_snapshot.owner_hash != owner_hash:
            order_snapshot = None

        role_snapshot = role_snapshots.get(character_id)
        if role_snapshot and (
            role_snapshot.owner_hash != owner_hash
            or role_snapshot.corporation_id != corporation_id
        ):
            role_snapshot = None
        if has_roles_scope and corporation_id is not None:
            try:
                role_snapshot, _ = refresh_character_role_snapshot(
                    character_id,
                    corporation_id,
                    owner_hash,
                )
            except Exception:
                logger.warning(
                    "Could not refresh corporation roles for character %s.",
                    character_id,
                    exc_info=True,
                )

        asset_status = _cache_status(
            snapshot.expires_at if snapshot else None,
            has_asset_scope,
            now,
            fetched_at=snapshot.fetched_at if snapshot else None,
        )
        contract_status = _cache_status(
            contract_snapshot.expires_at if contract_snapshot else None,
            has_contract_scope,
            now,
            fetched_at=contract_snapshot.fetched_at if contract_snapshot else None,
        )
        order_status = _cache_status(
            order_snapshot.expires_at if order_snapshot else None,
            has_order_scope,
            now,
            fetched_at=order_snapshot.fetched_at if order_snapshot else None,
        )
        contract_items_status = _contract_items_cache_status(
            contract_snapshot,
            contract_item_snapshots.get(character_id, []),
            owner_hash,
            has_contract_scope,
            now,
        )
        endpoint_statuses = {
            "assets": asset_status,
            "contracts": contract_status,
            "contract_items": contract_items_status,
            "orders": order_status,
        }
        expires_at = snapshot.expires_at if snapshot else None
        if expires_at is None or expires_at < now:
            freshness_class = "text-danger"
            freshness_label = "Missing expiry" if expires_at is None else "Expired"
        elif expires_at <= amber_limit:
            freshness_class = "text-warning"
            freshness_label = "Expiring soon"
        else:
            freshness_class = "text-success"
            freshness_label = "Fresh"

        characters.append(
            {
                "character_id": character_id,
                "character_name": character_name,
                "corporation_id": corporation_id,
                "corporation_name": corporation_name,
                "owner_hash": owner_hash,
                "portrait_url": (
                    "https://images.evetech.net/characters/"
                    f"{character_id}/portrait?size=64"
                ),
                "snapshot": snapshot,
                "contracts_snapshot": contract_snapshot,
                "contract_item_snapshots": contract_item_snapshots.get(
                    character_id, []
                ),
                "orders_snapshot": order_snapshot,
                "endpoints": endpoint_statuses,
                "endpoint_rows": _endpoint_rows(endpoint_statuses),
                "expires_at": expires_at,
                "freshness_class": freshness_class,
                "freshness_label": freshness_label,
                "has_asset_scope": has_asset_scope,
                "has_contract_scope": has_contract_scope,
                "has_order_scope": has_order_scope,
                "has_roles_scope": has_roles_scope,
                "has_corporation_asset_scope": has_corporation_asset_scope,
                "has_corporation_contract_scope": (
                    character_id,
                    owner_hash,
                )
                in corporation_contract_token_pairs,
                "has_corporation_order_scope": (
                    character_id,
                    owner_hash,
                )
                in corporation_order_token_pairs,
                "is_director": bool(
                    role_snapshot
                    and role_snapshot.expires_at
                    and role_snapshot.expires_at >= now
                    and "Director" in role_snapshot.roles
                ),
                "refreshable": any(
                    endpoint["refreshable"]
                    for endpoint in endpoint_statuses.values()
                ),
            }
        )
    return characters


def get_corporation_statuses(characters):
    directors_by_corporation = {}
    for character in characters:
        corporation_id = character.get("corporation_id")
        if character.get("is_director") and corporation_id is not None:
            directors_by_corporation.setdefault(corporation_id, []).append(character)

    snapshots = {
        snapshot.corporation_id: snapshot
        for snapshot in CorporationAssetSnapshot.objects.filter(
            corporation_id__in=directors_by_corporation
        )
    }
    contract_snapshots = {
        snapshot.corporation_id: snapshot
        for snapshot in CorporationContractSnapshot.objects.filter(
            corporation_id__in=directors_by_corporation
        )
    }
    order_snapshots = {
        snapshot.corporation_id: snapshot
        for snapshot in CorporationOrderSnapshot.objects.filter(
            corporation_id__in=directors_by_corporation
        )
    }
    contract_item_snapshots = {}
    for snapshot in ContractItemSnapshot.objects.filter(
        owner_type=ContractItemSnapshot.OwnerType.CORPORATION,
        owner_id__in=directors_by_corporation,
    ):
        contract_item_snapshots.setdefault(snapshot.owner_id, []).append(snapshot)
    now = timezone.now()
    amber_limit = now + timezone.timedelta(minutes=45)
    corporations = []
    for corporation_id, directors in directors_by_corporation.items():
        directors.sort(
            key=lambda character: (
                -sum(
                    bool(character.get(scope_key))
                    for scope_key in (
                        "has_corporation_asset_scope",
                        "has_corporation_contract_scope",
                        "has_corporation_order_scope",
                    )
                ),
                not character.get("has_corporation_asset_scope", False),
                character["character_id"],
            )
        )
        director = directors[0]
        snapshot = snapshots.get(corporation_id)
        if snapshot and (
            snapshot.owner_hash != director["owner_hash"]
            or snapshot.director_character_id != director["character_id"]
        ):
            snapshot = None
        contract_snapshot = contract_snapshots.get(corporation_id)
        if contract_snapshot and (
            contract_snapshot.owner_hash != director["owner_hash"]
            or contract_snapshot.director_character_id != director["character_id"]
        ):
            contract_snapshot = None
        order_snapshot = order_snapshots.get(corporation_id)
        if order_snapshot and (
            order_snapshot.owner_hash != director["owner_hash"]
            or order_snapshot.director_character_id != director["character_id"]
        ):
            order_snapshot = None

        has_asset_scope = director.get("has_corporation_asset_scope", False)
        has_contract_scope = director.get("has_corporation_contract_scope", False)
        has_order_scope = director.get("has_corporation_order_scope", False)
        asset_status = _cache_status(
            snapshot.expires_at if snapshot else None,
            has_asset_scope,
            now,
            fetched_at=snapshot.fetched_at if snapshot else None,
        )
        contract_status = _cache_status(
            contract_snapshot.expires_at if contract_snapshot else None,
            has_contract_scope,
            now,
            fetched_at=contract_snapshot.fetched_at if contract_snapshot else None,
        )
        order_status = _cache_status(
            order_snapshot.expires_at if order_snapshot else None,
            has_order_scope,
            now,
            fetched_at=order_snapshot.fetched_at if order_snapshot else None,
        )
        contract_items_status = _contract_items_cache_status(
            contract_snapshot,
            contract_item_snapshots.get(corporation_id, []),
            director["owner_hash"],
            has_contract_scope,
            now,
        )
        endpoint_statuses = {
            "assets": asset_status,
            "contracts": contract_status,
            "contract_items": contract_items_status,
            "orders": order_status,
        }

        corporation_name = next(
            (
                character["corporation_name"]
                for character in directors
                if character["corporation_name"]
            ),
            f"Corporation {corporation_id}",
        )
        corporations.append(
            {
                "corporation_id": corporation_id,
                "corporation_name": corporation_name,
                "logo_url": (
                    "https://images.evetech.net/corporations/"
                    f"{corporation_id}/logo?size=64"
                ),
                "snapshot": snapshot,
                "contracts_snapshot": contract_snapshot,
                "contract_item_snapshots": contract_item_snapshots.get(
                    corporation_id, []
                ),
                "orders_snapshot": order_snapshot,
                "endpoints": endpoint_statuses,
                "endpoint_rows": _endpoint_rows(endpoint_statuses),
                "expires_at": asset_status["expires_at"],
                "freshness_class": asset_status["freshness_class"],
                "freshness_label": asset_status["freshness_label"],
                "has_asset_scope": has_asset_scope,
                "has_contract_scope": has_contract_scope,
                "has_order_scope": has_order_scope,
                "director_character_id": director["character_id"],
                "owner_hash": director["owner_hash"],
                "refreshable": any(
                    endpoint["refreshable"]
                    for endpoint in endpoint_statuses.values()
                ),
            }
        )
    return sorted(
        corporations,
        key=lambda corporation: (
            corporation["corporation_name"].casefold(),
            corporation["corporation_id"],
        ),
    )


def _is_finished_contract(contract):
    return str(contract.get("status") or "").casefold().startswith("finished")


def _search_cached_assets(
    characters,
    target_type_ids,
    target_market_group_ids,
    target_solar_system_ids,
    corporations=(),
):
    expanded_type_ids = set(target_type_ids)
    expanded_type_ids.update(
        lookup_inventory_type_ids_for_market_groups(target_market_group_ids)
    )
    assets = []
    contracts = []
    orders = []
    asset_locations = {}
    contract_locations = {}
    order_locations = {}

    def add_locations(snapshot, destination):
        if snapshot is None:
            return
        for location_id, location in snapshot.locations.items():
            destination[int(location_id)] = ResolvedLocation(**location)

    def owner_metadata(owner, corporation=False):
        if corporation:
            return {
                "audit_owner_name": owner["corporation_name"],
                "audit_owner_type": "corporation",
                "audit_owner_icon_url": owner["logo_url"],
                "character_id": owner.get("director_character_id"),
            }
        return {
            "character_id": owner["character_id"],
            "audit_owner_name": owner["character_name"],
            "audit_owner_type": "character",
            "audit_owner_icon_url": owner.get("portrait_url")
            or (
                "https://images.evetech.net/characters/"
                f"{owner['character_id']}/portrait?size=64"
            ),
        }

    for character in characters:
        metadata = owner_metadata(character)
        snapshot = character.get("snapshot")
        if character.get("has_asset_scope") and snapshot is not None:
            assets.extend({**asset, **metadata} for asset in snapshot.assets)
            add_locations(snapshot, asset_locations)

        contract_snapshot = character.get("contracts_snapshot")
        if character.get("has_contract_scope") and contract_snapshot is not None:
            add_locations(contract_snapshot, contract_locations)
            item_snapshots = {
                item_snapshot.contract_id: item_snapshot
                for item_snapshot in character.get("contract_item_snapshots", [])
                if item_snapshot.owner_hash == character.get("owner_hash")
            }
            for contract in contract_snapshot.contracts:
                if _is_finished_contract(contract):
                    continue
                contract_id = int(contract["contract_id"])
                item_snapshot = item_snapshots.get(contract_id)
                location_id = int(contract.get("start_location_id") or 0)
                if item_snapshot is None or location_id <= 0:
                    continue
                for item in item_snapshot.items:
                    if item.get("is_included") is False:
                        continue
                    contracts.append(
                        {
                            **metadata,
                            "item_id": int(item.get("record_id") or contract_id),
                            "type_id": int(item["type_id"]),
                            "location_id": location_id,
                            "quantity": int(item.get("quantity") or 1),
                            "is_singleton": bool(item.get("is_singleton", False)),
                            "audit_result_type": "Contract",
                            "audit_reference": (
                                f"Contract {contract_id}"
                                + (f": {contract['title']}" if contract.get("title") else "")
                            ),
                        }
                    )

        order_snapshot = character.get("orders_snapshot")
        if character.get("has_order_scope") and order_snapshot is not None:
            add_locations(order_snapshot, order_locations)
            orders.extend(
                {
                    **metadata,
                    "item_id": int(order["order_id"]),
                    "type_id": int(order["type_id"]),
                    "location_id": int(order["location_id"]),
                    "quantity": int(order["volume_remain"]),
                    "is_singleton": False,
                    "audit_result_type": "Market",
                    "audit_reference": f"Order {order['order_id']}",
                }
                for order in order_snapshot.orders
            )

    for corporation in corporations:
        metadata = owner_metadata(corporation, corporation=True)
        snapshot = corporation.get("snapshot")
        if corporation.get("has_asset_scope") and snapshot is not None:
            assets.extend({**asset, **metadata} for asset in snapshot.assets)
            add_locations(snapshot, asset_locations)

        contract_snapshot = corporation.get("contracts_snapshot")
        if corporation.get("has_contract_scope") and contract_snapshot is not None:
            add_locations(contract_snapshot, contract_locations)
            item_snapshots = {
                item_snapshot.contract_id: item_snapshot
                for item_snapshot in corporation.get("contract_item_snapshots", [])
                if item_snapshot.owner_hash == corporation.get("owner_hash")
                and item_snapshot.director_character_id
                == corporation.get("director_character_id")
            }
            for contract in contract_snapshot.contracts:
                if _is_finished_contract(contract):
                    continue
                contract_id = int(contract["contract_id"])
                item_snapshot = item_snapshots.get(contract_id)
                location_id = int(contract.get("start_location_id") or 0)
                if item_snapshot is None or location_id <= 0:
                    continue
                for item in item_snapshot.items:
                    if item.get("is_included") is False:
                        continue
                    contracts.append(
                        {
                            **metadata,
                            "item_id": int(item.get("record_id") or contract_id),
                            "type_id": int(item["type_id"]),
                            "location_id": location_id,
                            "quantity": int(item.get("quantity") or 1),
                            "is_singleton": bool(item.get("is_singleton", False)),
                            "audit_result_type": "Contract",
                            "audit_reference": (
                                f"Contract {contract_id}"
                                + (f": {contract['title']}" if contract.get("title") else "")
                            ),
                        }
                    )

        order_snapshot = corporation.get("orders_snapshot")
        if corporation.get("has_order_scope") and order_snapshot is not None:
            add_locations(order_snapshot, order_locations)
            orders.extend(
                {
                    **metadata,
                    "item_id": int(order["order_id"]),
                    "type_id": int(order["type_id"]),
                    "location_id": int(order["location_id"]),
                    "quantity": int(order["volume_remain"]),
                    "is_singleton": False,
                    "audit_result_type": "Market",
                    "audit_reference": f"Order {order['order_id']}",
                }
                for order in order_snapshot.orders
            )

    def find_matches(records, locations, default_type=None):
        matches = matching_assets(
            records,
            expanded_type_ids,
            target_solar_system_ids,
            locations,
        )
        for match in matches:
            if default_type == "asset":
                match["audit_result_type"] = (
                    "Assembled Asset" if match.get("is_singleton") else "Packaged Asset"
                )
        return matches

    return (
        find_matches(assets, asset_locations, "asset")
        + find_matches(contracts, contract_locations)
        + find_matches(orders, order_locations)
    )


def _build_match_groups(matches, characters):
    character_names = {
        character["character_id"]: character["character_name"]
        for character in characters
    }
    type_names = dict(
        InventoryTypeReference.objects.filter(
            pk__in={match["type_id"] for match in matches}
        ).values_list("id", "name")
    )
    groups_by_location = {}
    for match in matches:
        character_id = match.get("character_id")
        owner_name = match.get("audit_owner_name") or character_names.get(
            character_id, "Unknown character"
        )
        owner_type = match.get("audit_owner_type", "character")
        owner_icon_url = match.get("audit_owner_icon_url") or (
            "https://images.evetech.net/characters/"
            f"{character_id}/portrait?size=64"
        )
        solar_system_name = (
            match.get("resolved_solar_system_name") or "Unknown solar system"
        )
        location_name = match.get("resolved_location_name") or "Unknown location"
        location_prefix = f"{solar_system_name} - "
        if location_name.casefold().startswith(location_prefix.casefold()):
            location_name = location_name[len(location_prefix) :]
        elif location_name.casefold() == solar_system_name.casefold():
            location_name = "System"

        location_key = (
            match.get("resolved_solar_system_id"),
            solar_system_name,
            match.get("resolved_location_id"),
            match.get("resolved_location_name"),
        )
        if location_key not in groups_by_location:
            groups_by_location[location_key] = {
                "solar_system_name": solar_system_name,
                "location_name": location_name,
                "matches": [],
            }
        groups_by_location[location_key]["matches"].append(
            {
                "owner_name": owner_name,
                "owner_type": owner_type,
                "owner_icon_url": owner_icon_url,
                "type_name": type_names.get(match["type_id"], "Unknown type"),
                "type_icon_url": (
                    f"https://images.evetech.net/types/{match['type_id']}/icon?size=32"
                ),
                "result_type": match.get("audit_result_type")
                or (
                    "Assembled Asset"
                    if match.get("is_singleton")
                    else "Packaged Asset"
                ),
                "reference": match.get("audit_reference", ""),
                "is_assembled": match.get("is_singleton", False),
                "quantity": match.get("quantity", 1),
            }
        )
    return list(groups_by_location.values())


def _remember_search(user, cleaned_data, search_parameters):
    strategy = {
        "target_type_ids": sorted(set(cleaned_data["target_type_names"])),
        "target_market_group_ids": sorted(
            set(cleaned_data["target_market_group_names"])
        ),
        "target_solar_system_ids": sorted(
            set(cleaned_data["target_solar_system_names"])
        ),
    }
    strategy_json = json.dumps(strategy, sort_keys=True, separators=(",", ":"))
    strategy_key = hashlib.sha256(strategy_json.encode("utf-8")).hexdigest()
    query = {
        "item_type_names": search_parameters["item_type_names"],
        "market_group_names": search_parameters["market_group_names"],
        "solar_system_names": search_parameters["solar_system_names"],
    }

    with transaction.atomic():
        saved_search, _ = RecentAssetAuditSearch.objects.update_or_create(
            user=user,
            strategy_key=strategy_key,
            defaults={"query": query, "last_used_at": timezone.now()},
        )
        old_search_ids = list(
            RecentAssetAuditSearch.objects.filter(user=user)
            .order_by("-last_used_at", "-pk")
            .values_list("pk", flat=True)[10:]
        )
        if old_search_ids:
            RecentAssetAuditSearch.objects.filter(pk__in=old_search_ids).delete()

    return saved_search


def index(request):
    if not request.user.has_perm("asset_audit.run_alliance_asset_audit"):
        return render(request, "asset_audit/no_permission.html")

    form = AssetAuditForm(request.POST or None)
    characters = get_character_statuses()
    corporations = get_corporation_statuses(characters)
    search_parameters = None
    csv_description = ""
    matches = []
    match_groups = []
    search_performed = False
    if request.method == "POST" and form.is_valid():
        search_performed = True
        matches = _search_cached_assets(
            characters,
            form.cleaned_data["target_type_names"],
            form.cleaned_data["target_market_group_names"],
            form.cleaned_data["target_solar_system_names"],
            corporations,
        )
        type_names_by_id = dict(
            InventoryTypeReference.objects.filter(
                pk__in=form.cleaned_data["target_type_names"]
            ).values_list("id", "name")
        )
        market_group_names_by_id = market_group_display_names(
            form.cleaned_data["target_market_group_names"]
        )
        system_names_by_id = dict(
            SolarSystemReference.objects.filter(
                pk__in=form.cleaned_data["target_solar_system_names"]
            ).values_list("id", "name")
        )
        search_parameters = {
            "item_type_names": list(
                dict.fromkeys(
                    type_names_by_id.get(type_id, "Unknown type")
                    for type_id in form.cleaned_data["target_type_names"]
                )
            ),
            "market_group_names": list(
                dict.fromkeys(
                    market_group_names_by_id.get(
                        group_id, "Unknown market group"
                    )
                    for group_id in form.cleaned_data["target_market_group_names"]
                )
            ),
            "solar_system_names": [
                system_names_by_id.get(system_id, "Unknown system")
                for system_id in form.cleaned_data["target_solar_system_names"]
            ],
        }
        search_types = [
            *search_parameters["item_type_names"],
            *search_parameters["market_group_names"],
        ]
        csv_description = (
            f'All {", ".join(search_types)} in '
            f'"{", ".join(search_parameters["solar_system_names"])}"'
        )
        _remember_search(request.user, form.cleaned_data, search_parameters)
        match_groups = _build_match_groups(matches, characters)

    recent_searches = RecentAssetAuditSearch.objects.filter(user=request.user)[:10]
    return render(
        request,
        "asset_audit/index.html",
        {
            "form": form,
            "characters": characters,
            "corporations": corporations,
            "search_parameters": search_parameters,
            "csv_description": csv_description,
            "matches": matches,
            "match_groups": match_groups,
            "search_performed": search_performed,
            "recent_searches": recent_searches,
            "refresh_url_prefix": request.path.rstrip("/") + "/refresh/",
            "refresh_corporation_url_prefix": request.path.rstrip("/")
            + "/refresh/corporation/",
            "solar_system_reference_url": request.path.rstrip("/")
            + "/reference/solar-systems/",
            "item_type_reference_url": request.path.rstrip("/")
            + "/reference/item-types/",
            "market_group_reference_url": request.path.rstrip("/")
            + "/reference/market-groups/",
        },
    )


@require_POST
def refresh_character_assets(request, character_id: int):
    if not request.user.has_perm("asset_audit.run_alliance_asset_audit"):
        return JsonResponse({"error": "Permission denied."}, status=403)

    endpoints = {}
    snapshots = {}
    snapshots["assets"] = _run_snapshot_refresh(
        "assets",
        refresh_character_snapshot,
        (character_id,),
        endpoints,
    )
    snapshots["contracts"] = _run_snapshot_refresh(
        "contracts",
        refresh_character_contract_snapshot,
        (character_id,),
        endpoints,
    )
    _refresh_character_contract_items_response(
        character_id,
        snapshots["contracts"],
        endpoints,
    )
    snapshots["orders"] = _run_snapshot_refresh(
        "orders",
        refresh_character_order_snapshot,
        (character_id,),
        endpoints,
    )
    asset_snapshot = snapshots["assets"]
    return JsonResponse(
        {
            "character_id": character_id,
            "fetched_at": (
                asset_snapshot.fetched_at.isoformat() if asset_snapshot else None
            ),
            "expires_at": (
                asset_snapshot.expires_at.isoformat()
                if asset_snapshot and asset_snapshot.expires_at
                else None
            ),
            "etag": asset_snapshot.etag if asset_snapshot else "",
            "not_modified": endpoints.get("assets", {}).get("not_modified", False),
            "endpoints": endpoints,
        }
    )


@require_POST
def refresh_corporation_assets(request, corporation_id: int):
    if not request.user.has_perm("asset_audit.run_alliance_asset_audit"):
        return JsonResponse({"error": "Permission denied."}, status=403)

    corporations = get_corporation_statuses(get_character_statuses())
    corporation = next(
        (
            item
            for item in corporations
            if item["corporation_id"] == corporation_id
        ),
        None,
    )
    if corporation is None:
        return JsonResponse(
            {"error": "No currently authenticated Director was found."},
            status=409,
        )

    director_args = (
        corporation_id,
        corporation["director_character_id"],
        corporation["owner_hash"],
    )
    endpoints = {}
    snapshots = {}
    snapshots["assets"] = _run_snapshot_refresh(
        "corporation assets",
        refresh_corporation_snapshot,
        director_args,
        endpoints,
        result_key="assets",
    )
    snapshots["contracts"] = _run_snapshot_refresh(
        "corporation contracts",
        refresh_corporation_contract_snapshot,
        director_args,
        endpoints,
        result_key="contracts",
    )
    _refresh_corporation_contract_items_response(
        *director_args,
        snapshots["contracts"],
        endpoints,
    )
    snapshots["orders"] = _run_snapshot_refresh(
        "corporation orders",
        refresh_corporation_order_snapshot,
        director_args,
        endpoints,
        result_key="orders",
    )
    asset_snapshot = snapshots["assets"]
    return JsonResponse(
        {
            "corporation_id": corporation_id,
            "fetched_at": (
                asset_snapshot.fetched_at.isoformat() if asset_snapshot else None
            ),
            "expires_at": (
                asset_snapshot.expires_at.isoformat()
                if asset_snapshot and asset_snapshot.expires_at
                else None
            ),
            "etag": asset_snapshot.etag if asset_snapshot else "",
            "not_modified": endpoints.get("assets", {}).get("not_modified", False),
            "endpoints": endpoints,
        }
    )


def _run_snapshot_refresh(label, refresh, args, endpoints, *, result_key=None):
    result_key = result_key or label
    try:
        snapshot, not_modified = refresh(*args)
    except Exception as error:
        logger.exception("Could not refresh %s.", label)
        endpoints[result_key] = _endpoint_response_error(error)
        return None
    endpoints[result_key] = _endpoint_response(
        snapshot.expires_at,
        etag=snapshot.etag,
        fetched_at=snapshot.fetched_at,
        not_modified=not_modified,
    )
    return snapshot


def _refresh_character_contract_items_response(
    character_id,
    contracts_snapshot,
    endpoints,
):
    from asset_audit.models import CharacterContractSnapshot

    contracts_snapshot = contracts_snapshot or CharacterContractSnapshot.objects.filter(
        character_id=character_id
    ).first()
    _refresh_contract_items_response(
        lambda: refresh_character_contract_items(character_id),
        contracts_snapshot,
        endpoints,
    )


def _refresh_corporation_contract_items_response(
    corporation_id,
    director_character_id,
    owner_hash,
    contracts_snapshot,
    endpoints,
):
    from asset_audit.models import CorporationContractSnapshot

    contracts_snapshot = contracts_snapshot or CorporationContractSnapshot.objects.filter(
        corporation_id=corporation_id,
        director_character_id=director_character_id,
        owner_hash=owner_hash,
    ).first()
    _refresh_contract_items_response(
        lambda: refresh_corporation_contract_items(
            corporation_id,
            director_character_id,
            owner_hash,
        ),
        contracts_snapshot,
        endpoints,
    )


def _refresh_contract_items_response(refresh, contracts_snapshot, endpoints):
    if contracts_snapshot is None:
        endpoints["contract_items"] = _endpoint_response(
            None,
            not_applicable=True,
            not_applicable_label="Waiting for contracts",
        )
        return
    relevant_contracts = [
        contract
        for contract in contracts_snapshot.contracts
        if contract.get("type") in {"item_exchange", "auction"}
    ]
    if not relevant_contracts:
        endpoints["contract_items"] = _endpoint_response(
            None,
            not_applicable=True,
            not_applicable_label="No item contracts",
        )
        return
    try:
        snapshots, errors = refresh()
    except Exception as error:
        logger.exception("Could not refresh contract item endpoints.")
        endpoints["contract_items"] = _endpoint_response_error(error)
        return
    if errors:
        endpoints["contract_items"] = _endpoint_response_error(
            RuntimeError("; ".join(errors))
        )
        return
    expected_ids = {int(contract["contract_id"]) for contract in relevant_contracts}
    snapshots_by_id = {
        snapshot.contract_id: snapshot for snapshot in snapshots
    }
    if expected_ids - set(snapshots_by_id):
        endpoints["contract_items"] = _endpoint_response(None)
        return
    expiries = [
        snapshots_by_id[contract_id].expires_at
        for contract_id in expected_ids
    ]
    expires_at = None if any(value is None for value in expiries) else min(expiries)
    endpoints["contract_items"] = _endpoint_response(
        expires_at,
        fetched_at=max(
            snapshots_by_id[contract_id].fetched_at
            for contract_id in expected_ids
        ),
    )


def _endpoint_response(
    expires_at,
    *,
    etag="",
    fetched_at=None,
    not_modified=False,
    not_applicable=False,
    not_applicable_label="Not applicable",
):
    status = _cache_status(
        expires_at,
        True,
        timezone.now(),
        fetched_at=fetched_at,
        not_applicable=not_applicable,
        not_applicable_label=not_applicable_label,
    )
    return {
        "expires_at": expires_at.isoformat() if expires_at else None,
        "etag": etag,
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "not_modified": not_modified,
        **status,
        "has_scope": True,
    }


def _endpoint_response_error(error):
    return {
        "error": str(error),
        "expires_at": None,
        "freshness_class": "text-danger",
        "freshness_label": str(error),
        "refreshable": True,
        "timer_active": False,
    }