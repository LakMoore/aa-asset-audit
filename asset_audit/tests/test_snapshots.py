import sys
import types
import unittest
from datetime import datetime, timezone as datetime_timezone
from unittest.mock import Mock, call, patch

from _django_test_setup import setup_django

setup_django()

from django.utils import timezone

from asset_audit.esi import AssetTokenNotFound
from asset_audit.matching import ResolvedLocation
from asset_audit.models import (
    CharacterAssetSnapshot,
    CharacterContractSnapshot,
    ContractItemSnapshot,
    CharacterRoleSnapshot,
    CorporationAssetSnapshot,
    CorporationContractSnapshot,
)
from asset_audit.snapshots import (
    AssetSnapshotAccessError,
    refresh_character_contract_items,
    refresh_character_contract_snapshot,
    refresh_character_role_snapshot,
    refresh_character_snapshot,
    refresh_corporation_snapshot,
    refresh_corporation_contract_snapshot,
)


def alliance_auth_modules(owner_hash):
    ownership_queryset = Mock()
    ownership_queryset.values_list.return_value.first.return_value = owner_hash
    ownership_model = Mock()
    ownership_model.objects.filter.return_value = ownership_queryset

    models_module = types.ModuleType("allianceauth.authentication.models")
    models_module.CharacterOwnership = ownership_model
    authentication_module = types.ModuleType("allianceauth.authentication")
    authentication_module.__path__ = []
    authentication_module.models = models_module
    allianceauth_module = types.ModuleType("allianceauth")
    allianceauth_module.__path__ = []
    allianceauth_module.authentication = authentication_module
    return {
        "allianceauth": allianceauth_module,
        "allianceauth.authentication": authentication_module,
        "allianceauth.authentication.models": models_module,
    }


class RefreshCharacterSnapshotTests(unittest.TestCase):
    def test_fresh_contract_snapshot_skips_esi(self):
        snapshot = CharacterContractSnapshot.objects.create(
            character_id=91218377,
            owner_hash="current-owner",
            contracts=[{"contract_id": 1001}],
            locations={},
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            etag='"fresh-contracts"',
        )
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch("asset_audit.snapshots.fetch_character_contracts_with_metadata") as fetch,
        ):
            refreshed, unchanged = refresh_character_contract_snapshot(91218377)

        self.assertEqual(refreshed.pk, snapshot.pk)
        self.assertTrue(unchanged)
        fetch.assert_not_called()

    def test_contract_304_preserves_body_and_updates_expiry_and_etag(self):
        old_contracts = [{"contract_id": 1001, "type": "item_exchange"}]
        snapshot = CharacterContractSnapshot.objects.create(
            character_id=91218376,
            owner_hash="current-owner",
            contracts=old_contracts,
            locations={},
            fetched_at=timezone.now() - timezone.timedelta(minutes=10),
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
            etag='"old-contracts"',
        )
        headers = {
            "ETag": '"new-contracts"',
            "Cache-Control": "max-age=300",
            "Date": "Tue, 20 Oct 2026 12:00:00 GMT",
        }
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch(
                "asset_audit.snapshots.fetch_character_contracts_with_metadata",
                return_value=(None, headers, True, Mock()),
            ) as fetch,
            patch("asset_audit.snapshots.resolve_location_ids") as resolve,
        ):
            refreshed, unchanged = refresh_character_contract_snapshot(91218376)

        refreshed.refresh_from_db()
        self.assertTrue(unchanged)
        self.assertEqual(refreshed.contracts, old_contracts)
        self.assertEqual(refreshed.etag, '"new-contracts"')
        self.assertEqual(
            refreshed.expires_at,
            datetime(2026, 10, 20, 12, 5, tzinfo=datetime_timezone.utc),
        )
        fetch.assert_called_once_with(91218376, owner_hash="current-owner")
        resolve.assert_not_called()

    def test_contract_item_refresh_only_fetches_expired_item_endpoints(self):
        CharacterContractSnapshot.objects.create(
            character_id=91218375,
            owner_hash="current-owner",
            contracts=[
                {"contract_id": 1001, "type": "item_exchange"},
                {"contract_id": 1002, "type": "auction"},
            ],
            locations={},
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        ContractItemSnapshot.objects.create(
            owner_type=ContractItemSnapshot.OwnerType.CHARACTER,
            owner_id=91218375,
            owner_hash="current-owner",
            contract_id=1001,
            items=[{"type_id": 10, "quantity": 1}],
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(minutes=30),
            etag='"fresh-items"',
        )
        ContractItemSnapshot.objects.create(
            owner_type=ContractItemSnapshot.OwnerType.CHARACTER,
            owner_id=91218375,
            owner_hash="current-owner",
            contract_id=1002,
            items=[{"type_id": 20, "quantity": 1}],
            fetched_at=timezone.now() - timezone.timedelta(hours=2),
            expires_at=timezone.now() - timezone.timedelta(hours=1),
            etag='"old-items"',
        )
        headers = {"ETag": '"new-items"', "Cache-Control": "max-age=3600"}
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch(
                "asset_audit.snapshots.fetch_character_contract_items_with_metadata",
                return_value=([{"type_id": 21, "quantity": 2}], headers, False, Mock()),
            ) as fetch,
        ):
            snapshots, errors = refresh_character_contract_items(91218375)

        self.assertEqual(errors, [])
        self.assertEqual({snapshot.contract_id for snapshot in snapshots}, {1001, 1002})
        self.assertEqual(
            ContractItemSnapshot.objects.get(contract_id=1002).items,
            [{"type_id": 21, "quantity": 2}],
        )
        fetch.assert_called_once_with(
            91218375,
            1002,
            owner_hash="current-owner",
        )

    def test_contract_item_refresh_preserves_fetch_error(self):
        character_id = 91218374
        CharacterContractSnapshot.objects.create(
            character_id=character_id,
            owner_hash="current-owner",
            contracts=[{"contract_id": 1003, "type": "item_exchange"}],
            locations={},
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch(
                "asset_audit.snapshots.fetch_character_contract_items_with_metadata",
                side_effect=RuntimeError("ESI item request failed"),
            ),
            self.assertLogs("asset_audit.snapshots", level="WARNING") as logged,
        ):
            snapshots, errors = refresh_character_contract_items(character_id)

        self.assertEqual(snapshots, [])
        self.assertEqual(errors, ["Contract 1003: ESI item request failed"])
        self.assertIn("Could not refresh contract items", logged.output[0])

    def test_corporation_contract_locations_use_the_contract_fetch_token(self):
        corporation_id = 99000187
        director_character_id = 91218387
        owner_hash = "current-owner"
        structure_id = 1_000_000_000_501
        station_id = 60_000_501
        CharacterRoleSnapshot.objects.create(
            character_id=director_character_id,
            owner_hash=owner_hash,
            corporation_id=corporation_id,
            roles=["Director"],
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        contracts = [
            {
                "contract_id": 5001,
                "type": "courier",
                "start_location_id": structure_id,
                "end_location_id": station_id,
            }
        ]
        source_token = Mock(name="contracts token")
        headers = {
            "ETag": '"corp-contracts-1"',
            "Cache-Control": "max-age=300",
        }
        locations = {
            structure_id: ResolvedLocation(
                structure_id,
                "Test Structure",
                30000142,
                "Jita",
            ),
            station_id: ResolvedLocation(
                station_id,
                "Test Station",
                30000142,
                "Jita",
            ),
        }
        with (
            patch.dict(sys.modules, alliance_auth_modules(owner_hash)),
            patch(
                "asset_audit.snapshots.fetch_corporation_contracts_with_metadata",
                return_value=(contracts, headers, False, source_token),
            ) as fetch,
            patch(
                "asset_audit.snapshots.resolve_location_ids",
                return_value=(locations, set()),
            ) as resolve,
        ):
            snapshot, unchanged = refresh_corporation_contract_snapshot(
                corporation_id,
                director_character_id,
                owner_hash,
            )

        self.assertFalse(unchanged)
        self.assertIsInstance(snapshot, CorporationContractSnapshot)
        self.assertEqual(snapshot.contracts, contracts)
        self.assertEqual(snapshot.locations[str(structure_id)]["name"], "Test Structure")
        self.assertEqual(snapshot.etag, '"corp-contracts-1"')
        fetch.assert_called_once_with(
            corporation_id,
            director_character_id,
            owner_hash=owner_hash,
        )
        resolve.assert_called_once_with(
            {structure_id, station_id},
            director_character_id,
            owner_hash,
            structure_token=source_token,
        )

    def test_fresh_character_snapshot_skips_esi(self):
        snapshot = CharacterAssetSnapshot.objects.create(
            character_id=91218378,
            owner_hash="current-owner",
            assets=[{"item_id": 456, "type_id": 789}],
            locations={},
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
            etag='"fresh"',
        )
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch("asset_audit.snapshots.fetch_character_assets_with_metadata") as fetch,
        ):
            refreshed, unchanged = refresh_character_snapshot(91218378)

        self.assertEqual(refreshed.pk, snapshot.pk)
        self.assertTrue(unchanged)
        fetch.assert_not_called()

    def test_not_modified_updates_metadata_and_preserves_local_assets(self):
        snapshot = CharacterAssetSnapshot.objects.create(
            character_id=91218379,
            owner_hash="current-owner",
            assets=[{"item_id": 456, "type_id": 789}],
            locations={},
            fetched_at=timezone.now(),
            expires_at=None,
            etag='"old"',
        )
        headers = {
            "ETag": '"new"',
            "Cache-Control": "max-age=2700",
            "Date": "Tue, 20 Oct 2026 12:00:00 GMT",
        }

        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch(
                "asset_audit.snapshots.fetch_character_assets_with_metadata",
                return_value=(None, headers, True),
            ) as fetch,
            patch("asset_audit.snapshots.resolve_asset_locations") as resolve,
        ):
            refreshed, not_modified = refresh_character_snapshot(91218379)

        refreshed.refresh_from_db()
        self.assertTrue(not_modified)
        self.assertEqual(refreshed.assets, [{"item_id": 456, "type_id": 789}])
        self.assertEqual(refreshed.etag, '"new"')
        self.assertEqual(
            refreshed.expires_at,
            datetime(2026, 10, 20, 12, 45, tzinfo=datetime_timezone.utc),
        )
        fetch.assert_called_once_with(91218379, owner_hash="current-owner")
        resolve.assert_not_called()

    def test_missing_local_snapshot_forces_a_full_fetch_after_304(self):
        assets = [{"item_id": 456, "type_id": 789, "location_id": 30}]
        headers = {"ETag": '"version-1"', "Cache-Control": "max-age=300"}
        location = ResolvedLocation(30, "Jita IV - Moon 4", 30000142, "Jita")
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch(
                "asset_audit.snapshots.fetch_character_assets_with_metadata",
                side_effect=[
                    (None, headers, True),
                    (assets, headers, False),
                ],
            ) as fetch,
            patch(
                "asset_audit.snapshots.resolve_asset_locations",
                return_value=({30: location}, set()),
            ),
        ):
            snapshot, not_modified = refresh_character_snapshot(91218380)

        self.assertFalse(not_modified)
        self.assertEqual(snapshot.assets, assets)
        self.assertEqual(snapshot.locations["30"]["name"], "Jita IV - Moon 4")
        fetch.assert_has_calls(
            [
                call(91218380, owner_hash="current-owner"),
                call(91218380, owner_hash="current-owner", force_refresh=True),
            ]
        )

    def test_missing_asset_token_is_reported_as_access_error(self):
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch(
                "asset_audit.snapshots.fetch_character_assets_with_metadata",
                side_effect=AssetTokenNotFound("missing"),
            ),
        ):
            with self.assertRaises(AssetSnapshotAccessError):
                refresh_character_snapshot(91218381)

    def test_refresh_character_roles_persists_roles_and_cache_headers(self):
        headers = {
            "ETag": '"roles-1"',
            "Cache-Control": "max-age=2700",
            "Date": "Tue, 20 Oct 2026 12:00:00 GMT",
        }
        with patch(
            "asset_audit.snapshots.fetch_character_roles_with_metadata",
            return_value=(["Director", "Accountant"], headers, False),
        ) as fetch:
            snapshot, not_modified = refresh_character_role_snapshot(
                91218382,
                99000182,
                "current-owner",
            )

        self.assertFalse(not_modified)
        self.assertEqual(snapshot.roles, ["Director", "Accountant"])
        self.assertEqual(snapshot.etag, '"roles-1"')
        self.assertEqual(
            snapshot.expires_at,
            datetime(2026, 10, 20, 12, 45, tzinfo=datetime_timezone.utc),
        )
        fetch.assert_called_once_with(91218382, owner_hash="current-owner")

    def test_role_304_preserves_roles_and_updates_cache_metadata(self):
        snapshot = CharacterRoleSnapshot.objects.create(
            character_id=91218383,
            owner_hash="current-owner",
            corporation_id=99000183,
            roles=["Director"],
            fetched_at=timezone.now(),
            expires_at=None,
            etag='"old"',
        )
        headers = {
            "ETag": '"new"',
            "Cache-Control": "max-age=2700",
            "Date": "Tue, 20 Oct 2026 12:00:00 GMT",
        }
        with patch(
            "asset_audit.snapshots.fetch_character_roles_with_metadata",
            return_value=(None, headers, True),
        ):
            refreshed, not_modified = refresh_character_role_snapshot(
                91218383,
                99000183,
                "current-owner",
            )

        refreshed.refresh_from_db()
        self.assertTrue(not_modified)
        self.assertEqual(refreshed.roles, ["Director"])
        self.assertEqual(refreshed.etag, '"new"')
        self.assertEqual(
            refreshed.expires_at,
            datetime(2026, 10, 20, 12, 45, tzinfo=datetime_timezone.utc),
        )

    def test_corporation_refresh_requires_cached_director_roles(self):
        with (
            patch.dict(sys.modules, alliance_auth_modules("current-owner")),
            patch("asset_audit.snapshots.fetch_corporation_assets_with_metadata") as fetch,
        ):
            with self.assertRaises(AssetSnapshotAccessError):
                refresh_corporation_snapshot(
                    99000184,
                    91218384,
                    "current-owner",
                )

        fetch.assert_not_called()

    def test_corporation_refresh_rejects_expired_director_roles(self):
        corporation_id = 99000186
        director_character_id = 91218386
        owner_hash = "current-owner"
        CharacterRoleSnapshot.objects.create(
            character_id=director_character_id,
            owner_hash=owner_hash,
            corporation_id=corporation_id,
            roles=["Director"],
            fetched_at=timezone.now() - timezone.timedelta(hours=2),
            expires_at=timezone.now() - timezone.timedelta(hours=1),
        )

        with (
            patch.dict(sys.modules, alliance_auth_modules(owner_hash)),
            patch("asset_audit.snapshots.fetch_corporation_assets_with_metadata") as fetch,
        ):
            with self.assertRaises(AssetSnapshotAccessError):
                refresh_corporation_snapshot(
                    corporation_id,
                    director_character_id,
                    owner_hash,
                )

        fetch.assert_not_called()

    def test_corporation_refresh_persists_assets_locations_and_headers(self):
        corporation_id = 99000185
        director_character_id = 91218385
        owner_hash = "current-owner"
        CharacterRoleSnapshot.objects.create(
            character_id=director_character_id,
            owner_hash=owner_hash,
            corporation_id=corporation_id,
            roles=["Director"],
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        assets = [{"item_id": 456, "type_id": 789, "location_id": 30}]
        headers = {
            "ETag": '"corp-assets-1"',
            "Cache-Control": "max-age=2700",
            "Date": "Tue, 20 Oct 2026 12:00:00 GMT",
        }
        location = ResolvedLocation(30, "Jita IV - Moon 4", 30000142, "Jita")
        with (
            patch.dict(sys.modules, alliance_auth_modules(owner_hash)),
            patch(
                "asset_audit.snapshots.fetch_corporation_assets_with_metadata",
                return_value=(assets, headers, False),
            ) as fetch,
            patch(
                "asset_audit.snapshots.resolve_asset_locations",
                return_value=({30: location}, set()),
            ) as resolve,
        ):
            snapshot, not_modified = refresh_corporation_snapshot(
                corporation_id,
                director_character_id,
                owner_hash,
            )

        self.assertFalse(not_modified)
        self.assertEqual(snapshot.assets, assets)
        self.assertEqual(snapshot.locations["30"]["name"], "Jita IV - Moon 4")
        self.assertEqual(snapshot.etag, '"corp-assets-1"')
        self.assertEqual(
            snapshot.expires_at,
            datetime(2026, 10, 20, 12, 45, tzinfo=datetime_timezone.utc),
        )
        fetch.assert_called_once_with(
            corporation_id,
            director_character_id,
            owner_hash=owner_hash,
        )
        resolve.assert_called_once_with(assets, director_character_id, owner_hash)


if __name__ == "__main__":
    unittest.main()