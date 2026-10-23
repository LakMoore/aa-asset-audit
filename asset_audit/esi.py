import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from typing import Any

from esi.exceptions import HTTPNotModified

ASSET_READ_SCOPE = "esi-assets.read_assets.v1"
CHARACTER_ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"
CORPORATION_ASSET_READ_SCOPE = "esi-assets.read_corporation_assets.v1"
CHARACTER_CONTRACTS_READ_SCOPE = "esi-contracts.read_character_contracts.v1"
CORPORATION_CONTRACTS_READ_SCOPE = "esi-contracts.read_corporation_contracts.v1"
CHARACTER_ORDERS_READ_SCOPE = "esi-markets.read_character_orders.v1"
CORPORATION_ORDERS_READ_SCOPE = "esi-markets.read_corporation_orders.v1"
STRUCTURE_READ_SCOPE = "esi-universe.read_structures.v1"
ESI_COMPATIBILITY_DATE = "2026-08-18"
_ESI_PROVIDER: Any = None


class AssetTokenNotFound(Exception):
    """Raised when a character has no token with the required asset scope."""


def _get_esi_provider() -> Any:
    global _ESI_PROVIDER
    if _ESI_PROVIDER is None:
        from esi.openapi_clients import ESIClientProvider

        _ESI_PROVIDER = ESIClientProvider(
            compatibility_date=ESI_COMPATIBILITY_DATE,
            ua_appname="AssetAudit",
            ua_version="0.1.0",
            operations=[
                "GetCharactersCharacterIdAssets",
                "GetCharactersCharacterIdRoles",
                "GetCorporationsCorporationIdAssets",
                "GetCharactersCharacterIdContracts",
                "GetCorporationsCorporationIdContracts",
                "GetCharactersCharacterIdContractsContractIdItems",
                "GetCorporationsCorporationIdContractsContractIdItems",
                "GetCharactersCharacterIdOrders",
                "GetCorporationsCorporationIdOrders",
                "GetUniverseStationsStationId",
                "GetUniverseStructuresStructureId",
                "PostUniverseNames",
            ],
        )
    return _ESI_PROVIDER


def _lookup_token(
    character_id: int,
    scopes: list[str],
    owner_hash: str | None = None,
) -> Any:
    from esi.models import Token

    if owner_hash is not None:
        return (
            Token.objects.filter(
                character_id=character_id,
                character_owner_hash=owner_hash,
            )
            .require_scopes(scopes)
            .first()
        )
    return Token.get_token(character_id, scopes)


def _asset_mapping(asset: Any) -> dict[str, Any]:
    if isinstance(asset, Mapping):
        return dict(asset)
    return asset.model_dump(mode="json")


def _operation_result_mapping(operation: Any) -> dict[str, Any]:
    result = operation.result()
    if isinstance(result, Mapping):
        return dict(result)
    return result.model_dump()


def fetch_station_details(
    station_id: int,
    *,
    esi_provider: Any = None,
) -> dict[str, Any]:
    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Universe.GetUniverseStationsStationId(
        station_id=station_id
    )
    return _operation_result_mapping(operation)


def fetch_structure_details(
    structure_id: int,
    token: Any,
    *,
    esi_provider: Any = None,
) -> dict[str, Any]:
    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Universe.GetUniverseStructuresStructureId(
        structure_id=structure_id,
        token=token,
    )
    return _operation_result_mapping(operation)


def fetch_character_names(
    character_ids: list[int],
    *,
    esi_provider: Any = None,
) -> dict[int, str]:
    if not character_ids:
        return {}

    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Universe.PostUniverseNames(body=character_ids)
    results = operation.result()
    names = {}
    for result in results:
        value = _asset_mapping(result)
        if value.get("category") == "character" and value.get("name"):
            names[int(value["id"])] = value["name"]
    return names


def fetch_character_assets(
    character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch every asset page for a character with an existing scoped token."""
    operation = _character_assets_operation(
        character_id,
        token_lookup=token_lookup,
        esi_provider=esi_provider,
        owner_hash=owner_hash,
    )
    return [_asset_mapping(asset) for asset in operation.results()]


def fetch_character_assets_with_metadata(
    character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool]:
    """Fetch assets using django-esi caching and return the final page headers."""
    operation = _character_assets_operation(
        character_id,
        token_lookup=token_lookup,
        esi_provider=esi_provider,
        owner_hash=owner_hash,
    )
    options = {"force_refresh": True} if force_refresh else {}
    try:
        assets, response = operation.results(return_response=True, **options)
    except HTTPNotModified as error:
        return None, error.headers, True
    return (
        [_asset_mapping(asset) for asset in assets],
        getattr(response, "headers", {}),
        False,
    )


def fetch_character_roles_with_metadata(
    character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    force_refresh: bool = False,
) -> tuple[list[str] | None, Mapping[str, Any], bool]:
    token_lookup = token_lookup or partial(_lookup_token, owner_hash=owner_hash)
    token = token_lookup(character_id, [CHARACTER_ROLES_SCOPE])
    if token is None or token is False:
        raise AssetTokenNotFound(
            f"Character {character_id} has no token with the corporation-roles scope."
        )

    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Character.GetCharactersCharacterIdRoles(
        character_id=character_id,
        token=token,
    )
    options = {"force_refresh": True} if force_refresh else {}
    try:
        result, response = operation.result(return_response=True, **options)
    except HTTPNotModified as error:
        return None, error.headers, True
    return (
        list(_asset_mapping(result).get("roles", [])),
        getattr(response, "headers", {}),
        False,
    )


def fetch_corporation_assets_with_metadata(
    corporation_id: int,
    director_character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool]:
    token_lookup = token_lookup or partial(_lookup_token, owner_hash=owner_hash)
    token = token_lookup(director_character_id, [CORPORATION_ASSET_READ_SCOPE])
    if token is None or token is False:
        raise AssetTokenNotFound(
            "Director character has no token with the corporation-asset scope."
        )

    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Assets.GetCorporationsCorporationIdAssets(
        corporation_id=corporation_id,
        token=token,
    )
    options = {"force_refresh": True} if force_refresh else {}
    try:
        assets, response = operation.results(return_response=True, **options)
    except HTTPNotModified as error:
        return None, error.headers, True
    return (
        [_asset_mapping(asset) for asset in assets],
        getattr(response, "headers", {}),
        False,
    )


def fetch_character_contracts_with_metadata(
    character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    token: Any = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool, Any]:
    token = _endpoint_token(
        character_id,
        CHARACTER_CONTRACTS_READ_SCOPE,
        token_lookup,
        owner_hash,
        token,
    )
    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Contracts.GetCharactersCharacterIdContracts(
        character_id=character_id,
        token=token,
    )
    contracts, headers, not_modified = _fetch_results_with_metadata(
        operation,
        force_refresh=force_refresh,
    )
    return contracts, headers, not_modified, token


def fetch_corporation_contracts_with_metadata(
    corporation_id: int,
    director_character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    token: Any = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool, Any]:
    token = _endpoint_token(
        director_character_id,
        CORPORATION_CONTRACTS_READ_SCOPE,
        token_lookup,
        owner_hash,
        token,
    )
    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Contracts.GetCorporationsCorporationIdContracts(
        corporation_id=corporation_id,
        token=token,
    )
    contracts, headers, not_modified = _fetch_results_with_metadata(
        operation,
        force_refresh=force_refresh,
    )
    return contracts, headers, not_modified, token


def fetch_character_contract_items_with_metadata(
    character_id: int,
    contract_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    token: Any = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool, Any]:
    token = _endpoint_token(
        character_id,
        CHARACTER_CONTRACTS_READ_SCOPE,
        token_lookup,
        owner_hash,
        token,
    )
    esi_provider = esi_provider or _get_esi_provider()
    operation = (
        esi_provider.client.Contracts.GetCharactersCharacterIdContractsContractIdItems(
            character_id=character_id,
            contract_id=contract_id,
            token=token,
        )
    )
    items, headers, not_modified = _fetch_results_with_metadata(
        operation,
        force_refresh=force_refresh,
    )
    return items, headers, not_modified, token


def fetch_corporation_contract_items_with_metadata(
    corporation_id: int,
    director_character_id: int,
    contract_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    token: Any = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool, Any]:
    token = _endpoint_token(
        director_character_id,
        CORPORATION_CONTRACTS_READ_SCOPE,
        token_lookup,
        owner_hash,
        token,
    )
    esi_provider = esi_provider or _get_esi_provider()
    operation = (
        esi_provider.client.Contracts.GetCorporationsCorporationIdContractsContractIdItems(
            corporation_id=corporation_id,
            contract_id=contract_id,
            token=token,
        )
    )
    items, headers, not_modified = _fetch_results_with_metadata(
        operation,
        force_refresh=force_refresh,
    )
    return items, headers, not_modified, token


def fetch_character_orders_with_metadata(
    character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    token: Any = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool, Any]:
    token = _endpoint_token(
        character_id,
        CHARACTER_ORDERS_READ_SCOPE,
        token_lookup,
        owner_hash,
        token,
    )
    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Market.GetCharactersCharacterIdOrders(
        character_id=character_id,
        token=token,
    )
    orders, headers, not_modified = _fetch_results_with_metadata(
        operation,
        force_refresh=force_refresh,
    )
    return orders, headers, not_modified, token


def fetch_corporation_orders_with_metadata(
    corporation_id: int,
    director_character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
    token: Any = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool, Any]:
    token = _endpoint_token(
        director_character_id,
        CORPORATION_ORDERS_READ_SCOPE,
        token_lookup,
        owner_hash,
        token,
    )
    esi_provider = esi_provider or _get_esi_provider()
    operation = esi_provider.client.Market.GetCorporationsCorporationIdOrders(
        corporation_id=corporation_id,
        token=token,
    )
    orders, headers, not_modified = _fetch_results_with_metadata(
        operation,
        force_refresh=force_refresh,
    )
    return orders, headers, not_modified, token


def _endpoint_token(
    character_id: int,
    scope: str,
    token_lookup: Callable[[int, list[str]], Any] | None,
    owner_hash: str | None,
    token: Any,
) -> Any:
    if token is not None:
        return token
    token_lookup = token_lookup or partial(_lookup_token, owner_hash=owner_hash)
    token = token_lookup(character_id, [scope])
    if token is None or token is False:
        raise AssetTokenNotFound(
            f"Character {character_id} has no token with the {scope} scope."
        )
    return token


def _fetch_results_with_metadata(
    operation: Any,
    *,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any], bool]:
    options = {"force_refresh": True} if force_refresh else {}
    try:
        results, response = operation.results(return_response=True, **options)
    except HTTPNotModified as error:
        return None, error.headers, True
    return (
        [_asset_mapping(result) for result in results],
        getattr(response, "headers", {}),
        False,
    )


def _character_assets_operation(
    character_id: int,
    *,
    token_lookup: Callable[[int, list[str]], Any] | None = None,
    esi_provider: Any = None,
    owner_hash: str | None = None,
) -> Any:
    token_lookup = token_lookup or partial(_lookup_token, owner_hash=owner_hash)
    token = token_lookup(character_id, [ASSET_READ_SCOPE])
    if token is None or token is False:
        raise AssetTokenNotFound(
            f"Character {character_id} has no token with the required asset scope."
        )

    esi_provider = esi_provider or _get_esi_provider()
    return esi_provider.client.Assets.GetCharactersCharacterIdAssets(
        character_id=character_id,
        token=token,
    )


def asset_expires_at_from_headers(
    headers: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the ESI response expiry represented by Cache-Control or Expires."""
    normalized_headers = {
        str(name).casefold(): str(value)
        for name, value in headers.items()
    }
    cache_control = normalized_headers.get("cache-control", "")
    max_age = re.search(r"(?:^|,)\s*max-age\s*=\s*\"?(\d+)", cache_control, re.I)
    if max_age:
        response_date = _parse_http_datetime(normalized_headers.get("date"))
        return (response_date or now or datetime.now(timezone.utc)) + timedelta(
            seconds=int(max_age.group(1))
        )

    expires_at = _parse_http_datetime(normalized_headers.get("expires"))
    if expires_at is not None:
        return expires_at
    return None


def _parse_http_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed