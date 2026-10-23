from collections.abc import Callable, Iterable
import logging
from typing import Any

from django.utils import timezone

from asset_audit.esi import (
    AssetTokenNotFound,
    asset_expires_at_from_headers,
    fetch_character_contract_items_with_metadata,
    fetch_character_contracts_with_metadata,
    fetch_character_orders_with_metadata,
    fetch_character_roles_with_metadata,
    fetch_character_assets_with_metadata,
    fetch_corporation_assets_with_metadata,
    fetch_corporation_contract_items_with_metadata,
    fetch_corporation_contracts_with_metadata,
    fetch_corporation_orders_with_metadata,
)
from asset_audit.location_resolution import (
    resolve_asset_locations,
    resolve_location_ids,
)
from asset_audit.models import (
    CharacterAssetSnapshot,
    CharacterContractSnapshot,
    CharacterOrderSnapshot,
    CharacterRoleSnapshot,
    CorporationAssetSnapshot,
    CorporationContractSnapshot,
    CorporationOrderSnapshot,
    ContractItemSnapshot,
)

logger = logging.getLogger(__name__)


class AssetSnapshotAccessError(Exception):
    """The character is not currently owned or lacks the asset-read scope."""


def refresh_character_snapshot(character_id: int) -> tuple[CharacterAssetSnapshot, bool]:
    from allianceauth.authentication.models import CharacterOwnership

    owner_hash = (
        CharacterOwnership.objects.filter(
            character__character_id=character_id,
            user__is_active=True,
        )
        .values_list("owner_hash", flat=True)
        .first()
    )
    if not owner_hash:
        raise AssetSnapshotAccessError(
            "Character is not currently authenticated on this server."
        )

    snapshot = CharacterAssetSnapshot.objects.filter(pk=character_id).first()
    if snapshot and snapshot.owner_hash != owner_hash:
        snapshot = None
    if snapshot and snapshot.expires_at and snapshot.expires_at > timezone.now():
        return snapshot, True

    try:
        assets, headers, not_modified = fetch_character_assets_with_metadata(
            character_id,
            owner_hash=owner_hash,
        )
    except AssetTokenNotFound as error:
        raise AssetSnapshotAccessError(
            "Character is missing the ESI asset-read permission."
        ) from error

    if not_modified and snapshot is not None:
        snapshot.fetched_at = timezone.now()
        snapshot.expires_at = asset_expires_at_from_headers(headers)
        snapshot.etag = _header(headers, "etag") or snapshot.etag
        snapshot.save(update_fields=["fetched_at", "expires_at", "etag"])
        return snapshot, True

    if not_modified:
        try:
            assets, headers, not_modified = fetch_character_assets_with_metadata(
                character_id,
                owner_hash=owner_hash,
                force_refresh=True,
            )
        except AssetTokenNotFound as error:
            raise AssetSnapshotAccessError(
                "Character is missing the ESI asset-read permission."
            ) from error
        if not_modified or assets is None:
            raise RuntimeError("ESI returned an unchanged response without a local snapshot.")

    if assets is None:
        raise RuntimeError("ESI did not return an asset snapshot.")

    locations, _ = resolve_asset_locations(
        assets,
        character_id,
        owner_hash,
    )

    location_data: dict[str, dict[str, Any]] = {
        str(location_id): {
            "location_id": location.location_id,
            "name": location.name,
            "solar_system_id": location.solar_system_id,
            "solar_system_name": location.solar_system_name,
        }
        for location_id, location in locations.items()
    }
    snapshot, _ = CharacterAssetSnapshot.objects.update_or_create(
        character_id=character_id,
        defaults={
            "owner_hash": owner_hash,
            "assets": assets,
            "locations": location_data,
            "fetched_at": timezone.now(),
            "expires_at": asset_expires_at_from_headers(headers),
            "etag": _header(headers, "etag"),
        },
    )
    return snapshot, False


def refresh_character_role_snapshot(
    character_id: int,
    corporation_id: int,
    owner_hash: str,
) -> tuple[CharacterRoleSnapshot, bool]:
    snapshot = CharacterRoleSnapshot.objects.filter(pk=character_id).first()
    if snapshot and (
        snapshot.owner_hash != owner_hash
        or snapshot.corporation_id != corporation_id
    ):
        snapshot = None
    if snapshot and snapshot.expires_at and snapshot.expires_at > timezone.now():
        return snapshot, True

    try:
        roles, headers, not_modified = fetch_character_roles_with_metadata(
            character_id,
            owner_hash=owner_hash,
        )
    except AssetTokenNotFound as error:
        raise AssetSnapshotAccessError(
            "Character is missing the corporation-roles permission."
        ) from error

    if not_modified and snapshot is not None:
        snapshot.fetched_at = timezone.now()
        snapshot.expires_at = asset_expires_at_from_headers(headers)
        snapshot.etag = _header(headers, "etag") or snapshot.etag
        snapshot.save(update_fields=["fetched_at", "expires_at", "etag"])
        return snapshot, True

    if not_modified:
        roles, headers, not_modified = fetch_character_roles_with_metadata(
            character_id,
            owner_hash=owner_hash,
            force_refresh=True,
        )
        if not_modified or roles is None:
            raise RuntimeError("ESI returned unchanged roles without a local snapshot.")

    if roles is None:
        raise RuntimeError("ESI did not return character roles.")

    snapshot, _ = CharacterRoleSnapshot.objects.update_or_create(
        character_id=character_id,
        defaults={
            "owner_hash": owner_hash,
            "corporation_id": corporation_id,
            "roles": roles,
            "fetched_at": timezone.now(),
            "expires_at": asset_expires_at_from_headers(headers),
            "etag": _header(headers, "etag"),
        },
    )
    return snapshot, False


def refresh_corporation_snapshot(
    corporation_id: int,
    director_character_id: int,
    owner_hash: str,
) -> tuple[CorporationAssetSnapshot, bool]:
    from allianceauth.authentication.models import CharacterOwnership

    is_current_owner = CharacterOwnership.objects.filter(
        character__character_id=director_character_id,
        character__corporation_id=corporation_id,
        owner_hash=owner_hash,
        user__is_active=True,
    ).exists()
    if not is_current_owner:
        raise AssetSnapshotAccessError(
            "Director character is not currently authenticated for this corporation."
        )

    role_snapshot = CharacterRoleSnapshot.objects.filter(
        character_id=director_character_id,
        owner_hash=owner_hash,
        corporation_id=corporation_id,
    ).first()
    if (
        role_snapshot is None
        or role_snapshot.expires_at is None
        or role_snapshot.expires_at < timezone.now()
        or "Director" not in role_snapshot.roles
    ):
        raise AssetSnapshotAccessError(
            "An authenticated Director character is required for corporation assets."
        )

    snapshot = CorporationAssetSnapshot.objects.filter(pk=corporation_id).first()
    if (
        snapshot
        and snapshot.owner_hash == owner_hash
        and snapshot.director_character_id == director_character_id
        and snapshot.expires_at
        and snapshot.expires_at > timezone.now()
    ):
        return snapshot, True
    try:
        assets, headers, not_modified = fetch_corporation_assets_with_metadata(
            corporation_id,
            director_character_id,
            owner_hash=owner_hash,
        )
    except AssetTokenNotFound as error:
        raise AssetSnapshotAccessError(
            "Director character is missing the corporation-asset permission."
        ) from error

    if not_modified and snapshot is not None:
        snapshot.fetched_at = timezone.now()
        snapshot.expires_at = asset_expires_at_from_headers(headers)
        snapshot.etag = _header(headers, "etag") or snapshot.etag
        snapshot.director_character_id = director_character_id
        snapshot.owner_hash = owner_hash
        snapshot.save(
            update_fields=[
                "fetched_at",
                "expires_at",
                "etag",
                "director_character_id",
                "owner_hash",
            ]
        )
        return snapshot, True

    if not_modified:
        assets, headers, not_modified = fetch_corporation_assets_with_metadata(
            corporation_id,
            director_character_id,
            owner_hash=owner_hash,
            force_refresh=True,
        )
        if not_modified or assets is None:
            raise RuntimeError(
                "ESI returned unchanged corporation assets without a local snapshot."
            )

    if assets is None:
        raise RuntimeError("ESI did not return a corporation asset snapshot.")

    locations, _ = resolve_asset_locations(
        assets,
        director_character_id,
        owner_hash,
    )
    location_data: dict[str, dict[str, Any]] = {
        str(location_id): {
            "location_id": location.location_id,
            "name": location.name,
            "solar_system_id": location.solar_system_id,
            "solar_system_name": location.solar_system_name,
        }
        for location_id, location in locations.items()
    }
    snapshot, _ = CorporationAssetSnapshot.objects.update_or_create(
        corporation_id=corporation_id,
        defaults={
            "director_character_id": director_character_id,
            "owner_hash": owner_hash,
            "assets": assets,
            "locations": location_data,
            "fetched_at": timezone.now(),
            "expires_at": asset_expires_at_from_headers(headers),
            "etag": _header(headers, "etag"),
        },
    )
    return snapshot, False


def refresh_character_contract_snapshot(
    character_id: int,
) -> tuple[CharacterContractSnapshot, bool]:
    owner_hash = _active_owner_hash(character_id)
    return _refresh_endpoint_snapshot(
        snapshot_model=CharacterContractSnapshot,
        key={"character_id": character_id},
        owner_identity={"owner_hash": owner_hash},
        payload_field="contracts",
        fetcher=fetch_character_contracts_with_metadata,
        fetch_args=(character_id,),
        fetch_kwargs={"owner_hash": owner_hash},
        source_character_id=character_id,
        owner_hash=owner_hash,
        location_ids=_contract_location_ids,
    )


def refresh_character_order_snapshot(
    character_id: int,
) -> tuple[CharacterOrderSnapshot, bool]:
    owner_hash = _active_owner_hash(character_id)
    return _refresh_endpoint_snapshot(
        snapshot_model=CharacterOrderSnapshot,
        key={"character_id": character_id},
        owner_identity={"owner_hash": owner_hash},
        payload_field="orders",
        fetcher=fetch_character_orders_with_metadata,
        fetch_args=(character_id,),
        fetch_kwargs={"owner_hash": owner_hash},
        source_character_id=character_id,
        owner_hash=owner_hash,
        location_ids=_order_location_ids,
    )


def refresh_corporation_contract_snapshot(
    corporation_id: int,
    director_character_id: int,
    owner_hash: str,
) -> tuple[CorporationContractSnapshot, bool]:
    _validate_corporation_director(
        corporation_id,
        director_character_id,
        owner_hash,
    )
    return _refresh_endpoint_snapshot(
        snapshot_model=CorporationContractSnapshot,
        key={"corporation_id": corporation_id},
        owner_identity={
            "director_character_id": director_character_id,
            "owner_hash": owner_hash,
        },
        payload_field="contracts",
        fetcher=fetch_corporation_contracts_with_metadata,
        fetch_args=(corporation_id, director_character_id),
        fetch_kwargs={"owner_hash": owner_hash},
        source_character_id=director_character_id,
        owner_hash=owner_hash,
        location_ids=_contract_location_ids,
    )


def refresh_corporation_order_snapshot(
    corporation_id: int,
    director_character_id: int,
    owner_hash: str,
) -> tuple[CorporationOrderSnapshot, bool]:
    _validate_corporation_director(
        corporation_id,
        director_character_id,
        owner_hash,
    )
    return _refresh_endpoint_snapshot(
        snapshot_model=CorporationOrderSnapshot,
        key={"corporation_id": corporation_id},
        owner_identity={
            "director_character_id": director_character_id,
            "owner_hash": owner_hash,
        },
        payload_field="orders",
        fetcher=fetch_corporation_orders_with_metadata,
        fetch_args=(corporation_id, director_character_id),
        fetch_kwargs={"owner_hash": owner_hash},
        source_character_id=director_character_id,
        owner_hash=owner_hash,
        location_ids=_order_location_ids,
    )


def refresh_character_contract_items(
    character_id: int,
) -> tuple[list[ContractItemSnapshot], list[str]]:
    owner_hash = _active_owner_hash(character_id)
    contract_snapshot = CharacterContractSnapshot.objects.filter(
        character_id=character_id,
        owner_hash=owner_hash,
    ).first()
    if contract_snapshot is None:
        return [], []
    return _refresh_contract_items(
        owner_type=ContractItemSnapshot.OwnerType.CHARACTER,
        owner_id=character_id,
        owner_hash=owner_hash,
        contracts=contract_snapshot.contracts,
        source_character_id=character_id,
        fetcher=fetch_character_contract_items_with_metadata,
        fetch_args=lambda contract_id: (character_id, contract_id),
    )


def refresh_corporation_contract_items(
    corporation_id: int,
    director_character_id: int,
    owner_hash: str,
) -> tuple[list[ContractItemSnapshot], list[str]]:
    _validate_corporation_director(
        corporation_id,
        director_character_id,
        owner_hash,
    )
    contract_snapshot = CorporationContractSnapshot.objects.filter(
        corporation_id=corporation_id,
        director_character_id=director_character_id,
        owner_hash=owner_hash,
    ).first()
    if contract_snapshot is None:
        return [], []
    return _refresh_contract_items(
        owner_type=ContractItemSnapshot.OwnerType.CORPORATION,
        owner_id=corporation_id,
        owner_hash=owner_hash,
        director_character_id=director_character_id,
        contracts=contract_snapshot.contracts,
        source_character_id=director_character_id,
        fetcher=fetch_corporation_contract_items_with_metadata,
        fetch_args=lambda contract_id: (
            corporation_id,
            director_character_id,
            contract_id,
        ),
    )


def _active_owner_hash(character_id: int) -> str:
    from allianceauth.authentication.models import CharacterOwnership

    owner_hash = (
        CharacterOwnership.objects.filter(
            character__character_id=character_id,
            user__is_active=True,
        )
        .values_list("owner_hash", flat=True)
        .first()
    )
    if not owner_hash:
        raise AssetSnapshotAccessError(
            "Character is not currently authenticated on this server."
        )
    return owner_hash


def _validate_corporation_director(
    corporation_id: int,
    director_character_id: int,
    owner_hash: str,
) -> None:
    from allianceauth.authentication.models import CharacterOwnership

    is_current_owner = CharacterOwnership.objects.filter(
        character__character_id=director_character_id,
        character__corporation_id=corporation_id,
        owner_hash=owner_hash,
        user__is_active=True,
    ).exists()
    if not is_current_owner:
        raise AssetSnapshotAccessError(
            "Director character is not currently authenticated for this corporation."
        )

    role_snapshot = CharacterRoleSnapshot.objects.filter(
        character_id=director_character_id,
        owner_hash=owner_hash,
        corporation_id=corporation_id,
    ).first()
    if (
        role_snapshot is None
        or role_snapshot.expires_at is None
        or role_snapshot.expires_at < timezone.now()
        or "Director" not in role_snapshot.roles
    ):
        raise AssetSnapshotAccessError(
            "An authenticated Director character is required for corporation data."
        )


def _refresh_endpoint_snapshot(
    *,
    snapshot_model: Any,
    key: dict[str, Any],
    owner_identity: dict[str, Any],
    payload_field: str,
    fetcher: Callable[..., tuple[list[dict[str, Any]] | None, Any, bool, Any]],
    fetch_args: tuple[Any, ...],
    fetch_kwargs: dict[str, Any],
    source_character_id: int,
    owner_hash: str,
    location_ids: Callable[[list[dict[str, Any]]], Iterable[int]],
) -> tuple[Any, bool]:
    snapshot = snapshot_model.objects.filter(**key).first()
    if snapshot and any(
        getattr(snapshot, field) != value
        for field, value in owner_identity.items()
    ):
        snapshot = None
    if snapshot and snapshot.expires_at and snapshot.expires_at > timezone.now():
        return snapshot, True

    try:
        payload, headers, not_modified, source_token = fetcher(
            *fetch_args,
            **fetch_kwargs,
        )
    except AssetTokenNotFound as error:
        raise AssetSnapshotAccessError(str(error)) from error

    if not_modified and snapshot is not None:
        snapshot.fetched_at = timezone.now()
        snapshot.expires_at = asset_expires_at_from_headers(headers)
        snapshot.etag = _header(headers, "etag") or snapshot.etag
        snapshot.save(
            update_fields=["fetched_at", "expires_at", "etag"]
        )
        return snapshot, True

    if not_modified:
        try:
            payload, headers, not_modified, source_token = fetcher(
                *fetch_args,
                **{**fetch_kwargs, "force_refresh": True},
            )
        except AssetTokenNotFound as error:
            raise AssetSnapshotAccessError(str(error)) from error
        if not_modified or payload is None:
            raise RuntimeError("ESI returned unchanged data without a local snapshot.")

    if payload is None:
        raise RuntimeError("ESI did not return endpoint snapshot data.")

    locations, _ = resolve_location_ids(
        location_ids(payload),
        source_character_id,
        owner_hash,
        structure_token=source_token,
    )
    location_data = _location_data(locations)
    defaults = {
        **owner_identity,
        payload_field: payload,
        "locations": location_data,
        "fetched_at": timezone.now(),
        "expires_at": asset_expires_at_from_headers(headers),
        "etag": _header(headers, "etag"),
    }
    snapshot, _ = snapshot_model.objects.update_or_create(
        **key,
        defaults=defaults,
    )
    return snapshot, False


def _refresh_contract_items(
    *,
    owner_type: str,
    owner_id: int,
    owner_hash: str,
    contracts: list[dict[str, Any]],
    source_character_id: int,
    fetcher: Callable[..., tuple[list[dict[str, Any]] | None, Any, bool, Any]],
    fetch_args: Callable[[int], tuple[Any, ...]],
    director_character_id: int | None = None,
) -> tuple[list[ContractItemSnapshot], list[str]]:
    errors = []
    for contract in contracts:
        if contract.get("type") not in {"item_exchange", "auction"}:
            continue
        contract_id = int(contract["contract_id"])
        snapshot = ContractItemSnapshot.objects.filter(
            owner_type=owner_type,
            owner_id=owner_id,
            contract_id=contract_id,
        ).first()
        if snapshot and (
            snapshot.owner_hash != owner_hash
            or snapshot.director_character_id != director_character_id
        ):
            snapshot = None
        if snapshot and snapshot.expires_at and snapshot.expires_at > timezone.now():
            continue

        kwargs = {"owner_hash": owner_hash}
        try:
            items, headers, not_modified, _ = fetcher(
                *fetch_args(contract_id),
                **kwargs,
            )
            if not_modified and snapshot is not None:
                snapshot.fetched_at = timezone.now()
                snapshot.expires_at = asset_expires_at_from_headers(headers)
                snapshot.etag = _header(headers, "etag") or snapshot.etag
                snapshot.save(update_fields=["fetched_at", "expires_at", "etag"])
                continue
            if not_modified:
                items, headers, not_modified, _ = fetcher(
                    *fetch_args(contract_id),
                    **{**kwargs, "force_refresh": True},
                )
                if not_modified or items is None:
                    raise RuntimeError(
                        "ESI returned unchanged contract items without a local snapshot."
                    )
            if items is None:
                raise RuntimeError("ESI did not return contract item data.")
            ContractItemSnapshot.objects.update_or_create(
                owner_type=owner_type,
                owner_id=owner_id,
                contract_id=contract_id,
                defaults={
                    "owner_hash": owner_hash,
                    "director_character_id": director_character_id,
                    "items": items,
                    "fetched_at": timezone.now(),
                    "expires_at": asset_expires_at_from_headers(headers),
                    "etag": _header(headers, "etag"),
                },
            )
        except AssetTokenNotFound as error:
            errors.append(str(error))
            break
        except Exception as error:
            logger.warning(
                "Could not refresh contract items for contract %s and character %s.",
                contract_id,
                source_character_id,
                exc_info=True,
            )
            errors.append(f"Contract {contract_id}: {error}")

    contract_ids = [
        int(contract["contract_id"])
        for contract in contracts
        if contract.get("type") in {"item_exchange", "auction"}
    ]
    snapshots = list(
        ContractItemSnapshot.objects.filter(
            owner_type=owner_type,
            owner_id=owner_id,
            owner_hash=owner_hash,
            contract_id__in=contract_ids,
        )
    )
    return snapshots, errors


def _contract_location_ids(contracts: list[dict[str, Any]]) -> set[int]:
    return {
        int(contract[key])
        for contract in contracts
        for key in ("start_location_id", "end_location_id")
        if contract.get(key) and int(contract[key]) > 0
    }


def _order_location_ids(orders: list[dict[str, Any]]) -> set[int]:
    return {
        int(order["location_id"])
        for order in orders
        if order.get("location_id") and int(order["location_id"]) > 0
    }


def _location_data(locations: dict[int, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(location_id): {
            "location_id": location.location_id,
            "name": location.name,
            "solar_system_id": location.solar_system_id,
            "solar_system_name": location.solar_system_name,
        }
        for location_id, location in locations.items()
    }


def _header(headers: Any, name: str) -> str:
    target = name.casefold()
    return str(
        next(
            (value for key, value in headers.items() if str(key).casefold() == target),
            "",
        )
    )