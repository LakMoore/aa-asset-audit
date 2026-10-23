import unittest
from unittest.mock import Mock, patch

from _django_test_setup import setup_django

setup_django()

from asset_audit.esi import STRUCTURE_READ_SCOPE
from asset_audit.location_resolution import (
    resolve_asset_locations,
    resolve_location_ids,
    terminal_location_ids,
)
from asset_audit.matching import ASSET_ID_THRESHOLD
from asset_audit.models import (
    SolarSystemReference,
    StationReference,
    StructureReference,
)


class AssetLocationResolutionTests(unittest.TestCase):
    def test_terminal_location_ids_follow_parent_assets_and_deduplicate(self):
        structure_id = ASSET_ID_THRESHOLD + 1
        parent_item_id = ASSET_ID_THRESHOLD + 2
        assets = [
            {
                "item_id": ASSET_ID_THRESHOLD + 3,
                "type_id": 10,
                "location_id": parent_item_id,
                "location_type": "item",
            },
            {
                "item_id": ASSET_ID_THRESHOLD + 4,
                "type_id": 11,
                "location_id": parent_item_id,
                "location_type": "item",
            },
            {
                "item_id": parent_item_id,
                "type_id": 20,
                "location_id": structure_id,
                "location_type": "item",
            },
            {
                "item_id": ASSET_ID_THRESHOLD + 5,
                "type_id": 21,
                "location_id": 60_000_001,
                "location_type": "station",
            },
            {
                "item_id": ASSET_ID_THRESHOLD + 6,
                "type_id": 22,
                "location_id": 30_000_142,
                "location_type": "solar_system",
            },
        ]

        self.assertEqual(
            terminal_location_ids(assets),
            {structure_id, 60_000_001, 30_000_142},
        )

    def test_resolves_and_caches_station_structure_and_system_locations(self):
        structure_id = ASSET_ID_THRESHOLD + 101
        station_id = 60_000_101
        system_id = 30_000_101
        SolarSystemReference.objects.update_or_create(
            id=system_id,
            defaults={"name": "Test System"},
        )
        assets = [
            {
                "item_id": ASSET_ID_THRESHOLD + 201,
                "type_id": 10,
                "location_id": structure_id,
                "location_type": "item",
            },
            {
                "item_id": ASSET_ID_THRESHOLD + 202,
                "type_id": 11,
                "location_id": structure_id,
                "location_type": "item",
            },
            {
                "item_id": ASSET_ID_THRESHOLD + 203,
                "type_id": 12,
                "location_id": station_id,
                "location_type": "station",
            },
            {
                "item_id": ASSET_ID_THRESHOLD + 204,
                "type_id": 13,
                "location_id": system_id,
                "location_type": "solar_system",
            },
        ]
        token = Mock()
        token_lookup = Mock(return_value=token)

        with (
            patch(
                "asset_audit.location_resolution.fetch_station_details",
                return_value={
                    "name": "Test Station",
                    "system_id": system_id,
                },
            ) as fetch_station,
            patch(
                "asset_audit.location_resolution.fetch_structure_details",
                return_value={
                    "name": "Test Structure",
                    "solar_system_id": system_id,
                },
            ) as fetch_structure,
        ):
            locations, unresolved = resolve_asset_locations(
                assets,
                123,
                "owner-hash",
                token_lookup=token_lookup,
            )
            cached_locations, cached_unresolved = resolve_asset_locations(
                assets,
                123,
                "owner-hash",
                token_lookup=token_lookup,
            )

        token_lookup.assert_called_once_with(
            123,
            [STRUCTURE_READ_SCOPE],
            "owner-hash",
        )
        fetch_station.assert_called_once_with(station_id, esi_provider=None)
        fetch_structure.assert_called_once_with(
            structure_id,
            token,
            esi_provider=None,
        )
        self.assertEqual(unresolved, set())
        self.assertEqual(cached_unresolved, set())
        self.assertEqual(locations[station_id].name, "Test Station")
        self.assertEqual(locations[structure_id].name, "Test Structure")
        self.assertEqual(locations[system_id].name, "Test System")
        self.assertEqual(cached_locations[structure_id].solar_system_name, "Test System")
        self.assertTrue(StationReference.objects.filter(station_id=station_id).exists())
        self.assertTrue(StructureReference.objects.filter(structure_id=structure_id).exists())

    def test_missing_structure_scope_leaves_structure_id_unresolved(self):
        structure_id = ASSET_ID_THRESHOLD + 301
        token_lookup = Mock(return_value=None)
        assets = [
            {
                "item_id": ASSET_ID_THRESHOLD + 302,
                "type_id": 10,
                "location_id": structure_id,
                "location_type": "item",
            }
        ]

        with patch(
            "asset_audit.location_resolution.fetch_structure_details"
        ) as fetch_structure:
            locations, unresolved = resolve_asset_locations(
                assets,
                456,
                "owner-hash",
                token_lookup=token_lookup,
            )

        self.assertEqual(locations, {})
        self.assertEqual(unresolved, {structure_id})
        fetch_structure.assert_not_called()

    def test_direct_location_resolution_uses_the_supplied_source_token(self):
        structure_id = ASSET_ID_THRESHOLD + 401
        token = Mock()
        token_lookup = Mock()
        with patch(
            "asset_audit.location_resolution.fetch_structure_details",
            return_value={
                "name": "Source Structure",
                "solar_system_id": 30_000_401,
            },
        ) as fetch_structure:
            locations, unresolved = resolve_location_ids(
                [structure_id],
                789,
                "owner-hash",
                token_lookup=token_lookup,
                structure_token=token,
            )

        token_lookup.assert_not_called()
        fetch_structure.assert_called_once_with(
            structure_id,
            token,
            esi_provider=None,
        )
        self.assertEqual(locations[structure_id].name, "Source Structure")
        self.assertEqual(unresolved, set())


if __name__ == "__main__":
    unittest.main()