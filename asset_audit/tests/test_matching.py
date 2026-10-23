import unittest

from asset_audit.matching import ResolvedLocation, matching_assets


class MatchingAssetsTests(unittest.TestCase):
    def test_matches_type_and_system_through_nested_assets(self):
        assets = [
            {
                "item_id": 1_000_000_000_101,
                "type_id": 20,
                "location_id": 1000000000001,
                "location_type": "item",
            },
            {
                "item_id": 1_000_000_000_201,
                "type_id": 30,
                "location_id": 1_000_000_000_101,
                "location_type": "item",
            },
            {
                "item_id": 1_000_000_000_301,
                "type_id": 34,
                "location_id": 1_000_000_000_201,
                "location_type": "item",
            },
            {
                "item_id": 1_000_000_000_401,
                "type_id": 34,
                "location_id": 60000002,
                "location_type": "station",
            },
            {
                "item_id": 1_000_000_000_501,
                "type_id": 35,
                "location_id": 60000001,
                "location_type": "station",
            },
        ]

        result = matching_assets(
            assets,
            target_type_ids={34},
            target_solar_system_ids={30000142},
            locations_by_id={
                1000000000001: ResolvedLocation(
                    1000000000001, "Test Structure", 30000142, "Jita"
                ),
                60000002: ResolvedLocation(
                    60000002, "Test Station", 30002659, "Ashab"
                ),
            },
        )

        self.assertEqual([asset["item_id"] for asset in result], [1_000_000_000_301])
        self.assertEqual(result[0]["resolved_location_name"], "Test Structure")
        self.assertEqual(result[0]["resolved_solar_system_name"], "Jita")

    def test_ignores_orphaned_and_cyclic_asset_locations(self):
        assets = [
            {
                "item_id": 10,
                "type_id": 34,
                "location_id": 20,
                "location_type": "item",
            },
            {
                "item_id": 20,
                "type_id": 35,
                "location_id": 10,
                "location_type": "item",
            },
            {
                "item_id": 30,
                "type_id": 34,
                "location_id": 999,
                "location_type": "item",
            },
        ]

        result = matching_assets(
            assets,
            target_type_ids={34},
            target_solar_system_ids={30000142},
            locations_by_id={},
        )

        self.assertEqual(result, [])

    def test_empty_filter_matches_nothing(self):
        result = matching_assets(
            assets=[],
            target_type_ids={34},
            target_solar_system_ids=set(),
            locations_by_id={},
        )

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
