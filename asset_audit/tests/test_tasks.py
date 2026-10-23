import unittest
from unittest.mock import Mock, patch

from _django_test_setup import setup_django

setup_django()

from django.contrib.auth import get_user_model

from asset_audit.matching import ResolvedLocation
from asset_audit.models import (
    AssetAuditRun,
    InventoryTypeReference,
    MarketGroupReference,
)
from asset_audit.tasks import (
    get_authenticated_asset_characters,
    scan_character_assets,
    start_asset_audit,
)

def create_run(**kwargs):
    kwargs.setdefault("status", AssetAuditRun.Status.RUNNING)
    target_type_ids = kwargs.pop("target_type_ids", [10])
    target_solar_system_ids = kwargs.pop("target_solar_system_ids", [30])
    user = get_user_model().objects.create_user(
        username=f"user-{get_user_model().objects.count()}"
    )
    return AssetAuditRun.objects.create(
        requested_by=user,
        target_type_ids=target_type_ids,
        target_solar_system_ids=target_solar_system_ids,
        **kwargs,
    )


class AuthenticatedAssetCharactersTests(unittest.TestCase):
    def test_only_yields_characters_with_current_owner_scoped_tokens(self):
        ownership_queryset = Mock()
        ownership_queryset.values_list.return_value = [
            ("123", "current-owner"),
            ("456", "another-owner"),
        ]
        ownership_model = Mock()
        ownership_model.objects.filter.return_value = ownership_queryset

        token_queryset = Mock()
        token_queryset.values_list.return_value = [
            (123, "current-owner"),
            (456, "stale-owner"),
        ]
        token_model = Mock()
        token_model.objects.filter.return_value.require_scopes.return_value = token_queryset

        characters = list(
            get_authenticated_asset_characters(
                ownership_model,
                token_model,
            )
        )

        ownership_model.objects.filter.assert_called_once_with(
            user__is_active=True,
        )
        token_model.objects.filter.assert_called_once_with(
            character_id__in=[123, 456],
            character_owner_hash__in=["current-owner", "another-owner"],
        )
        self.assertEqual(characters, [(123, "current-owner")])


class AssetAuditTaskTests(unittest.TestCase):
    def test_market_group_types_are_ored_with_explicit_types(self):
        run = create_run(
            character_count=1,
            target_type_ids=[810001],
            target_market_group_ids=[800001, 800004],
        )
        MarketGroupReference.objects.update_or_create(
            id=800001,
            defaults={"name": "Ships", "parent_group_id": None},
        )
        MarketGroupReference.objects.update_or_create(
            id=800002,
            defaults={"name": "Cruisers", "parent_group_id": 800001},
        )
        MarketGroupReference.objects.update_or_create(
            id=800004,
            defaults={"name": "Selected second group", "parent_group_id": None},
        )
        InventoryTypeReference.objects.update_or_create(
            id=810001,
            defaults={"name": "Explicit type", "market_group_id": 800003},
        )
        InventoryTypeReference.objects.update_or_create(
            id=810002,
            defaults={"name": "Group type", "market_group_id": 800002},
        )
        InventoryTypeReference.objects.update_or_create(
            id=810003,
            defaults={"name": "Unselected type", "market_group_id": 800003},
        )
        InventoryTypeReference.objects.update_or_create(
            id=810004,
            defaults={"name": "Second group type", "market_group_id": 800004},
        )
        assets = [
            {
                "item_id": 1,
                "type_id": 810001,
                "location_id": 30,
                "location_type": "solar_system",
            },
            {
                "item_id": 2,
                "type_id": 810002,
                "location_id": 30,
                "location_type": "solar_system",
            },
            {
                "item_id": 3,
                "type_id": 810003,
                "location_id": 30,
                "location_type": "solar_system",
            },
            {
                "item_id": 4,
                "type_id": 810004,
                "location_id": 30,
                "location_type": "solar_system",
            },
        ]
        location = ResolvedLocation(30, "Jita", 30, "Jita")

        with (
            patch("asset_audit.tasks.fetch_character_assets", return_value=assets),
            patch(
                "asset_audit.tasks.resolve_asset_locations",
                return_value=({30: location}, set()),
            ),
        ):
            matches = scan_character_assets.run(run.pk, 123, "owner-hash")

        self.assertEqual(matches, 3)
        self.assertEqual(
            set(run.matches.values_list("type_id", flat=True)),
            {810001, 810002, 810004},
        )

    def test_character_task_resolves_locations_before_matching(self):
        run = create_run(character_count=1)
        assets = [
            {
                "item_id": 1_000_000_000_101,
                "type_id": 10,
                "location_id": 1_000_000_000_201,
                "location_type": "item",
            },
            {
                "item_id": 1_000_000_000_201,
                "type_id": 11,
                "location_id": 20,
                "location_type": "station",
            },
        ]
        location = ResolvedLocation(20, "Jita IV - Moon 4", 30, "Jita")

        with (
            patch("asset_audit.tasks.fetch_character_assets", return_value=assets) as fetch,
            patch(
                "asset_audit.tasks.resolve_asset_locations",
                return_value=({20: location}, set()),
            ) as resolve,
        ):
            matches = scan_character_assets.run(
                run.pk,
                123,
                "current-owner",
            )

        fetch.assert_called_once_with(123, owner_hash="current-owner")
        resolve.assert_called_once_with(assets, 123, "current-owner")
        self.assertEqual(matches, 1)
        run.refresh_from_db()
        self.assertEqual(run.status, AssetAuditRun.Status.COMPLETE)
        self.assertEqual(run.completed_character_count, 1)
        self.assertEqual(run.matches.count(), 1)
        self.assertEqual(run.matches.first().item_id, assets[0]["item_id"])
        self.assertEqual(run.matches.first().location_id, 20)
        self.assertEqual(run.matches.first().location_name, "Jita IV - Moon 4")
        self.assertEqual(run.matches.first().solar_system_name, "Jita")

    def test_duplicate_character_delivery_does_not_duplicate_matches_or_progress(self):
        run = create_run(character_count=1)
        assets = [
            {
                "item_id": 1,
                "type_id": 10,
                "location_id": 30,
                "location_type": "solar_system",
            }
        ]
        with (
            patch("asset_audit.tasks.fetch_character_assets", return_value=assets),
            patch(
                "asset_audit.tasks.resolve_asset_locations",
                return_value=(
                    {30: ResolvedLocation(30, "Jita", 30, "Jita")},
                    set(),
                ),
            ),
        ):
            scan_character_assets.run(run.pk, 123, "owner-a")
            scan_character_assets.run(run.pk, 123, "owner-a")

        run.refresh_from_db()
        self.assertEqual(run.completed_character_count, 1)
        self.assertEqual(run.matches.count(), 1)

    def test_start_task_schedules_one_job_per_eligible_character(self):
        run = create_run(status=AssetAuditRun.Status.PENDING)
        with (
            patch(
                "asset_audit.tasks.get_authenticated_asset_characters",
                return_value=[(123, "owner-a"), (456, "owner-b")],
            ) as get_characters,
            patch("asset_audit.tasks.scan_character_assets.delay") as schedule,
        ):
            count = start_asset_audit.run(run.pk)

        get_characters.assert_called_once_with()
        run.refresh_from_db()
        self.assertEqual(count, 2)
        self.assertEqual(run.status, AssetAuditRun.Status.RUNNING)
        self.assertEqual(run.character_count, 2)
        self.assertEqual(schedule.call_count, 2)
        schedule.assert_any_call(run.pk, 123, "owner-a")
        schedule.assert_any_call(run.pk, 456, "owner-b")

    def test_empty_character_set_completes_run(self):
        run = create_run(status=AssetAuditRun.Status.PENDING)
        with patch("asset_audit.tasks.get_authenticated_asset_characters", return_value=[]):
            count = start_asset_audit.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(run.status, AssetAuditRun.Status.COMPLETE)
        self.assertEqual(run.character_count, 0)

    def test_failed_character_is_counted_and_scan_completes_with_errors(self):
        run = create_run(character_count=1)
        with patch(
            "asset_audit.tasks.fetch_character_assets",
            side_effect=RuntimeError("ESI unavailable"),
        ):
            with self.assertLogs("asset_audit.tasks", level="ERROR"):
                self.assertEqual(scan_character_assets.run(run.pk, 123, "owner-a"), 0)

        run.refresh_from_db()
        self.assertEqual(run.status, AssetAuditRun.Status.COMPLETE_WITH_ERRORS)
        self.assertEqual(run.failed_character_count, 1)

    def test_unresolved_location_marks_character_scan_incomplete(self):
        run = create_run(character_count=1)
        assets = [{"item_id": 1, "type_id": 10, "location_id": 2}]
        with (
            patch("asset_audit.tasks.fetch_character_assets", return_value=assets),
            patch(
                "asset_audit.tasks.resolve_asset_locations",
                return_value=({}, {1_000_000_000_001}),
            ),
        ):
            self.assertEqual(
                scan_character_assets.run(run.pk, 123, "current-owner"),
                0,
            )

        run.refresh_from_db()
        self.assertEqual(run.status, AssetAuditRun.Status.COMPLETE_WITH_ERRORS)
        self.assertEqual(run.failed_character_count, 1)


if __name__ == "__main__":
    unittest.main()