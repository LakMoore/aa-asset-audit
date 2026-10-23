import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from esi.exceptions import HTTPNotModified
from pydantic import BaseModel

from asset_audit.esi import (
    ASSET_READ_SCOPE,
    CHARACTER_CONTRACTS_READ_SCOPE,
    CHARACTER_ORDERS_READ_SCOPE,
    CHARACTER_ROLES_SCOPE,
    CORPORATION_ASSET_READ_SCOPE,
    CORPORATION_CONTRACTS_READ_SCOPE,
    CORPORATION_ORDERS_READ_SCOPE,
    AssetTokenNotFound,
    _asset_mapping,
    asset_expires_at_from_headers,
    fetch_character_assets,
    fetch_character_assets_with_metadata,
    fetch_character_contract_items_with_metadata,
    fetch_character_contracts_with_metadata,
    fetch_character_names,
    fetch_character_orders_with_metadata,
    fetch_character_roles_with_metadata,
    fetch_corporation_assets_with_metadata,
    fetch_corporation_contract_items_with_metadata,
    fetch_corporation_contracts_with_metadata,
    fetch_corporation_orders_with_metadata,
    fetch_station_details,
    fetch_structure_details,
)


class FetchCharacterAssetsTests(unittest.TestCase):
    def test_asset_mapping_serializes_datetime_fields(self):
        class DatedResponse(BaseModel):
            issued: datetime

        issued = datetime(2026, 10, 21, 20, 19, tzinfo=timezone.utc)

        result = _asset_mapping(DatedResponse(issued=issued))

        self.assertEqual(
            datetime.fromisoformat(result["issued"].replace("Z", "+00:00")),
            issued,
        )

    def test_fetches_assets_with_scoped_refreshed_token(self):
        character_id = 123
        asset = Mock()
        asset.model_dump.return_value = {"item_id": 456, "type_id": 789}
        token = Mock()
        token.valid_access_token.return_value = "fresh-access-token"
        response = Mock()
        response.results.return_value = [asset]
        endpoint = Mock(return_value=response)
        provider = Mock()
        provider.client.Assets.GetCharactersCharacterIdAssets = endpoint
        token_lookup = Mock(return_value=token)

        result = fetch_character_assets(
            character_id,
            token_lookup=token_lookup,
            esi_provider=provider,
        )

        token_lookup.assert_called_once_with(character_id, [ASSET_READ_SCOPE])
        token.valid_access_token.assert_not_called()
        endpoint.assert_called_once_with(
            character_id=character_id,
            token=token,
        )
        response.results.assert_called_once_with()
        self.assertEqual(result, [{"item_id": 456, "type_id": 789}])

    def test_fetches_character_roles_and_returns_response_metadata(self):
        response = Mock()
        response.headers = {"ETag": '"roles-1"', "Cache-Control": "max-age=60"}
        operation = Mock()
        operation.result.return_value = ({"roles": ["Director"]}, response)
        provider = Mock()
        provider.client.Character.GetCharactersCharacterIdRoles.return_value = operation
        token = Mock()
        token_lookup = Mock(return_value=token)

        result = fetch_character_roles_with_metadata(
            123,
            token_lookup=token_lookup,
            esi_provider=provider,
        )

        token_lookup.assert_called_once_with(123, [CHARACTER_ROLES_SCOPE])
        provider.client.Character.GetCharactersCharacterIdRoles.assert_called_once_with(
            character_id=123,
            token=token,
        )
        operation.result.assert_called_once_with(return_response=True)
        self.assertEqual(result, (["Director"], response.headers, False))

    def test_character_roles_304_returns_headers(self):
        headers = {"ETag": '"roles-1"', "Cache-Control": "max-age=60"}
        operation = Mock()
        operation.result.side_effect = HTTPNotModified(304, headers)
        provider = Mock()
        provider.client.Character.GetCharactersCharacterIdRoles.return_value = operation

        result = fetch_character_roles_with_metadata(
            123,
            token_lookup=Mock(return_value=Mock()),
            esi_provider=provider,
        )

        self.assertEqual(result, (None, headers, True))

    def test_fetches_corporation_assets_with_director_scoped_token(self):
        response = Mock()
        response.headers = {"ETag": '"corp-assets-1"'}
        response.results.return_value = ([{"item_id": 456}], response)
        provider = Mock()
        provider.client.Assets.GetCorporationsCorporationIdAssets.return_value = response
        token = Mock()
        token_lookup = Mock(return_value=token)

        result = fetch_corporation_assets_with_metadata(
            456,
            123,
            token_lookup=token_lookup,
            esi_provider=provider,
        )

        token_lookup.assert_called_once_with(123, [CORPORATION_ASSET_READ_SCOPE])
        provider.client.Assets.GetCorporationsCorporationIdAssets.assert_called_once_with(
            corporation_id=456,
            token=token,
        )
        response.results.assert_called_once_with(return_response=True)
        self.assertEqual(result, ([{"item_id": 456}], response.headers, False))

    def test_corporation_assets_304_returns_headers(self):
        headers = {"ETag": '"corp-assets-1"', "Cache-Control": "max-age=60"}
        response = Mock()
        response.results.side_effect = HTTPNotModified(304, headers)
        provider = Mock()
        provider.client.Assets.GetCorporationsCorporationIdAssets.return_value = response

        result = fetch_corporation_assets_with_metadata(
            456,
            123,
            token_lookup=Mock(return_value=Mock()),
            esi_provider=provider,
        )

        self.assertEqual(result, (None, headers, True))

    def test_fetch_with_metadata_returns_assets_and_response_headers(self):
        response = Mock()
        response.headers = {"ETag": '"page-1"', "Cache-Control": "max-age=60"}
        response.results.return_value = ([{"item_id": 456}], response)
        provider = Mock()
        provider.client.Assets.GetCharactersCharacterIdAssets.return_value = response

        result = fetch_character_assets_with_metadata(
            123,
            token_lookup=Mock(return_value=Mock()),
            esi_provider=provider,
        )

        self.assertEqual(
            result,
            ([{"item_id": 456}], response.headers, False),
        )
        response.results.assert_called_once_with(return_response=True)

    def test_fetch_with_metadata_reports_not_modified_response(self):
        response = Mock()
        headers = {"ETag": '"page-1"', "Cache-Control": "max-age=60"}
        response.results.side_effect = HTTPNotModified(304, headers)
        provider = Mock()
        provider.client.Assets.GetCharactersCharacterIdAssets.return_value = response

        result = fetch_character_assets_with_metadata(
            123,
            token_lookup=Mock(return_value=Mock()),
            esi_provider=provider,
        )

        self.assertEqual(result, (None, headers, True))

    def test_contract_and_order_fetchers_use_the_expected_operations_and_scopes(self):
        cases = [
            (
                fetch_character_contracts_with_metadata,
                "Contracts",
                "GetCharactersCharacterIdContracts",
                (123,),
                {"character_id": 123},
                123,
                CHARACTER_CONTRACTS_READ_SCOPE,
            ),
            (
                fetch_corporation_contracts_with_metadata,
                "Contracts",
                "GetCorporationsCorporationIdContracts",
                (456, 123),
                {"corporation_id": 456},
                123,
                CORPORATION_CONTRACTS_READ_SCOPE,
            ),
            (
                fetch_character_contract_items_with_metadata,
                "Contracts",
                "GetCharactersCharacterIdContractsContractIdItems",
                (123, 789),
                {"character_id": 123, "contract_id": 789},
                123,
                CHARACTER_CONTRACTS_READ_SCOPE,
            ),
            (
                fetch_corporation_contract_items_with_metadata,
                "Contracts",
                "GetCorporationsCorporationIdContractsContractIdItems",
                (456, 123, 789),
                {"corporation_id": 456, "contract_id": 789},
                123,
                CORPORATION_CONTRACTS_READ_SCOPE,
            ),
            (
                fetch_character_orders_with_metadata,
                "Market",
                "GetCharactersCharacterIdOrders",
                (123,),
                {"character_id": 123},
                123,
                CHARACTER_ORDERS_READ_SCOPE,
            ),
            (
                fetch_corporation_orders_with_metadata,
                "Market",
                "GetCorporationsCorporationIdOrders",
                (456, 123),
                {"corporation_id": 456},
                123,
                CORPORATION_ORDERS_READ_SCOPE,
            ),
        ]
        for fetcher, namespace, operation_name, args, endpoint_kwargs, character_id, scope in cases:
            with self.subTest(operation=operation_name):
                operation = Mock()
                response = Mock(headers={"ETag": '"new"'})
                operation.results.return_value = ([{"type_id": 789}], response)
                client_namespace = Mock()
                endpoint = Mock(return_value=operation)
                setattr(client_namespace, operation_name, endpoint)
                provider = Mock()
                setattr(provider.client, namespace, client_namespace)
                token = Mock()
                token_lookup = Mock(return_value=token)

                result = fetcher(
                    *args,
                    token_lookup=token_lookup,
                    esi_provider=provider,
                    owner_hash="owner-hash",
                )

                token_lookup.assert_called_once_with(character_id, [scope])
                endpoint.assert_called_once_with(**endpoint_kwargs, token=token)
                operation.results.assert_called_once_with(return_response=True)
                self.assertEqual(
                    result,
                    ([{"type_id": 789}], {"ETag": '"new"'}, False, token),
                )

    def test_contract_item_fetcher_reuses_source_token_and_handles_304(self):
        headers = {"ETag": '"item-etag"', "Cache-Control": "max-age=3600"}
        operation = Mock()
        operation.results.side_effect = HTTPNotModified(304, headers)
        provider = Mock()
        provider.client.Contracts.GetCorporationsCorporationIdContractsContractIdItems.return_value = operation
        token = Mock()

        result = fetch_corporation_contract_items_with_metadata(
            456,
            123,
            789,
            token=token,
            esi_provider=provider,
            force_refresh=True,
        )

        provider.client.Contracts.GetCorporationsCorporationIdContractsContractIdItems.assert_called_once_with(
            corporation_id=456,
            contract_id=789,
            token=token,
        )
        operation.results.assert_called_once_with(
            return_response=True,
            force_refresh=True,
        )
        self.assertEqual(result, (None, headers, True, token))

    def test_expiry_uses_cache_control_max_age_and_response_date(self):
        expiry = asset_expires_at_from_headers(
            {
                "Cache-Control": "public, max-age=2700",
                "Date": "Tue, 20 Oct 2026 12:00:00 GMT",
            }
        )

        self.assertEqual(
            expiry,
            datetime(2026, 10, 20, 12, 45, tzinfo=timezone.utc),
        )

    def test_expiry_uses_expires_header_when_max_age_is_absent(self):
        expiry = asset_expires_at_from_headers(
            {"Expires": "Tue, 20 Oct 2026 12:45:00 GMT"}
        )

        self.assertEqual(
            expiry,
            datetime(2026, 10, 20, 12, 45, tzinfo=timezone.utc),
        )

    def test_raises_when_character_has_no_scoped_token(self):
        with self.assertRaises(AssetTokenNotFound):
            fetch_character_assets(
                123,
                token_lookup=Mock(return_value=False),
                esi_provider=Mock(),
            )

    def test_fetches_station_details(self):
        response = Mock()
        response.result.return_value.model_dump.return_value = {
            "station_id": 60000001,
            "name": "Test Station",
            "system_id": 30000142,
        }
        provider = Mock()
        provider.client.Universe.GetUniverseStationsStationId.return_value = response

        result = fetch_station_details(60000001, esi_provider=provider)

        provider.client.Universe.GetUniverseStationsStationId.assert_called_once_with(
            station_id=60000001
        )
        response.result.assert_called_once_with()
        self.assertEqual(result["system_id"], 30000142)

    def test_fetches_structure_details_with_token_object(self):
        response = Mock()
        response.result.return_value.model_dump.return_value = {
            "name": "Test Structure",
            "solar_system_id": 30000142,
        }
        provider = Mock()
        provider.client.Universe.GetUniverseStructuresStructureId.return_value = (
            response
        )
        token = Mock()

        result = fetch_structure_details(
            1_000_000_000_001,
            token,
            esi_provider=provider,
        )

        provider.client.Universe.GetUniverseStructuresStructureId.assert_called_once_with(
            structure_id=1_000_000_000_001,
            token=token,
        )
        response.result.assert_called_once_with()
        self.assertEqual(result["solar_system_id"], 30000142)

    def test_fetches_character_names_in_one_public_esi_request(self):
        operation = Mock()
        operation.result.return_value = [
            {
                "id": 123,
                "name": "Test Character",
                "category": "character",
            },
            {"id": 456, "name": "Jita", "category": "solar_system"},
        ]
        provider = Mock()
        provider.client.Universe.PostUniverseNames.return_value = operation

        result = fetch_character_names([123, 456], esi_provider=provider)

        provider.client.Universe.PostUniverseNames.assert_called_once_with(
            body=[123, 456]
        )
        self.assertEqual(result, {123: "Test Character"})


if __name__ == "__main__":
    unittest.main()