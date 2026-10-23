import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from _django_test_setup import setup_django

setup_django()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, override_settings
from django.utils import timezone

from asset_audit.matching import ResolvedLocation
from asset_audit.models import (
    AssetAuditRun,
    CharacterAssetSnapshot,
    CharacterContractSnapshot,
    CharacterOrderSnapshot,
    CharacterRoleSnapshot,
    CorporationAssetSnapshot,
    CorporationContractSnapshot,
    CorporationOrderSnapshot,
    ContractItemSnapshot,
    InventoryTypeReference,
    MarketGroupReference,
    RecentAssetAuditSearch,
    SolarSystemReference,
)


class AssetAuditViewTests(unittest.TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username=f"audit-page-user-{get_user_model().objects.count()}"
        )
        permission = Permission.objects.get(
            content_type__app_label="asset_audit",
            codename="run_alliance_asset_audit",
        )
        self.user.user_permissions.add(permission)
        self.client = Client()
        self.client.force_login(self.user)
        from asset_audit.views import get_character_statuses

        self.get_character_statuses = get_character_statuses
        statuses_patch = patch("asset_audit.views.get_character_statuses", return_value=[])
        statuses_patch.start()
        self.addCleanup(statuses_patch.stop)

    def test_recently_updated_short_cache_remains_fresh(self):
        from asset_audit.views import _cache_status

        now = timezone.now()
        expires_at = now + timezone.timedelta(minutes=3)

        recently_updated = _cache_status(
            expires_at,
            True,
            now,
            fetched_at=now,
        )
        older_update = _cache_status(
            expires_at,
            True,
            now,
            fetched_at=now - timezone.timedelta(minutes=6),
        )
        expired = _cache_status(
            now - timezone.timedelta(seconds=1),
            True,
            now,
            fetched_at=now,
        )

        self.assertEqual(recently_updated["freshness_class"], "text-success")
        self.assertEqual(older_update["freshness_class"], "text-warning")
        self.assertEqual(expired["freshness_class"], "text-danger")

    def test_search_uses_cached_assets_without_fetching_or_saving_a_run(self):
        InventoryTypeReference.objects.update_or_create(
            id=29990,
            defaults={"name": "Loki"},
        )
        SolarSystemReference.objects.update_or_create(
            id=31001783,
            defaults={"name": "J130330"},
        )
        snapshot, _ = CharacterAssetSnapshot.objects.update_or_create(
            character_id=91218379,
            defaults={
                "owner_hash": "current-owner",
                "assets": [
                    {
                        "item_id": 1055831652212,
                        "type_id": 29990,
                        "location_id": 31001783,
                        "location_type": "solar_system",
                        "quantity": 1,
                        "is_singleton": True,
                    }
                ],
                "locations": {
                    "31001783": {
                        "location_id": 31001783,
                        "name": "J130330",
                        "solar_system_id": 31001783,
                        "solar_system_name": "J130330",
                    }
                },
                "fetched_at": timezone.now(),
                "expires_at": timezone.now() + timezone.timedelta(hours=1),
            },
        )
        character = {
            "character_id": 91218379,
            "character_name": "Example Pilot",
            "snapshot": snapshot,
            "has_asset_scope": True,
            "refreshable": False,
        }
        with (
            patch("asset_audit.views.get_character_statuses", return_value=[character]),
            patch(
                "asset_audit.forms.lookup_inventory_type_ids",
                return_value={"loki": [29990]},
            ),
            patch(
                "asset_audit.forms.lookup_solar_system_ids",
                return_value={"j130330": [31001783]},
            ),
            patch(
                "asset_audit.views.lookup_inventory_type_ids_for_market_groups",
                return_value=[],
            ),
            patch("asset_audit.esi.fetch_character_assets_with_metadata") as fetch,
        ):
            response = self.client.post(
                "/",
                {
                    "target_type_names": "Loki",
                    "target_market_group_names": "",
                    "target_solar_system_names": "J130330",
                },
            )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Search results", content)
        self.assertIn("Example Pilot", content)
        self.assertIn("Loki", content)
        self.assertIn("J130330", content)
        fetch.assert_not_called()
        self.assertFalse(AssetAuditRun.objects.filter(requested_by=self.user).exists())

    def test_recent_search_is_unique_and_replays_with_current_character_data(self):
        InventoryTypeReference.objects.update_or_create(
            id=29990,
            defaults={"name": "Loki"},
        )
        SolarSystemReference.objects.update_or_create(
            id=30000142,
            defaults={"name": "Jita"},
        )
        first_character = {
            "character_id": 1,
            "character_name": "Example Pilot",
            "snapshot": "first",
        }
        latest_character = {
            "character_id": 1,
            "character_name": "Example Pilot",
            "snapshot": "latest",
        }
        with (
            patch(
                "asset_audit.views.get_character_statuses",
                side_effect=[[first_character], [latest_character]],
            ),
            patch(
                "asset_audit.forms.lookup_inventory_type_ids",
                return_value={"loki": [29990]},
            ),
            patch(
                "asset_audit.forms.lookup_solar_system_ids",
                return_value={"jita": [30000142]},
            ),
            patch(
                "asset_audit.views.lookup_inventory_type_ids_for_market_groups",
                return_value=[],
            ),
            patch("asset_audit.views._search_cached_assets", return_value=[]) as search,
        ):
            self.client.post(
                "/",
                {
                    "target_type_names": "Loki",
                    "target_market_group_names": "",
                    "target_solar_system_names": "Jita",
                },
            )
            response = self.client.post(
                "/",
                {
                    "target_type_names": "loki",
                    "target_market_group_names": "",
                    "target_solar_system_names": "jita",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            RecentAssetAuditSearch.objects.filter(user=self.user).count(),
            1,
        )
        self.assertEqual(search.call_args_list[0].args[0], [first_character])
        self.assertEqual(search.call_args_list[1].args[0], [latest_character])
        content = response.content.decode()
        self.assertIn("Recent searches", content)
        self.assertIn("Search again", content)
        self.assertIn('name="target_type_names" value="Loki"', content)

    def test_recent_search_history_keeps_only_the_ten_latest_strategies(self):
        from asset_audit.views import _remember_search

        for type_id in range(11):
            _remember_search(
                self.user,
                {
                    "target_type_names": [type_id],
                    "target_market_group_names": [],
                    "target_solar_system_names": [30000142],
                },
                {
                    "item_type_names": [f"Type {type_id}"],
                    "market_group_names": [],
                    "solar_system_names": ["Jita"],
                },
            )

        response = self.client.get("/")
        content = response.content.decode()

        self.assertEqual(
            RecentAssetAuditSearch.objects.filter(user=self.user).count(),
            10,
        )
        self.assertIn("Type 10", content)
        self.assertNotIn("Type 0", content)

    def test_duplicate_market_group_names_render_with_parent_labels(self):
        MarketGroupReference.objects.update_or_create(
            id=1368,
            defaults={"name": "Parent Alpha", "parent_group_id": None},
        )
        MarketGroupReference.objects.update_or_create(
            id=2064,
            defaults={"name": "Parent Beta", "parent_group_id": None},
        )
        MarketGroupReference.objects.update_or_create(
            id=1138,
            defaults={"name": "Strategic Cruisers", "parent_group_id": 1368},
        )
        MarketGroupReference.objects.update_or_create(
            id=2369,
            defaults={"name": "Strategic Cruisers", "parent_group_id": 2064},
        )
        SolarSystemReference.objects.update_or_create(
            id=31001783,
            defaults={"name": "J130330"},
        )
        with (
            patch(
                "asset_audit.forms.lookup_market_group_ids",
                return_value={"strategic cruisers": [1138, 2369]},
            ),
            patch(
                "asset_audit.forms.lookup_solar_system_ids",
                return_value={"j130330": [31001783]},
            ),
            patch(
                "asset_audit.views.lookup_inventory_type_ids_for_market_groups",
                return_value=[],
            ) as lookup_types,
        ):
            response = self.client.post(
                "/",
                {
                    "target_type_names": "",
                    "target_market_group_names": "Strategic Cruisers",
                    "target_solar_system_names": "J130330",
                },
            )

        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "<dd>Strategic Cruisers, Strategic Cruisers</dd>",
            content,
        )
        self.assertIn(
            "<dd>Strategic Cruisers (Parent Alpha), "
            "Strategic Cruisers (Parent Beta)</dd>",
            content,
        )
        self.assertIn(
            'data-description="All Strategic Cruisers (Parent Alpha), '
            'Strategic Cruisers (Parent Beta) in &quot;J130330&quot;"',
            content,
        )
        self.assertIn("Download CSV", content)
        saved_search = RecentAssetAuditSearch.objects.get(user=self.user)
        self.assertEqual(
            saved_search.query["market_group_names"],
            [
                "Strategic Cruisers (Parent Alpha)",
                "Strategic Cruisers (Parent Beta)",
            ],
        )
        lookup_types.assert_called_once_with([1138, 2369])

    def test_index_renders_name_only_autocomplete_for_both_fields(self):
        response = self.client.get("/")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Search assets", content)
        self.assertIn("Character and corporation cache", content)
        self.assertIn("window.setInterval(updateExpiryStatuses, 30_000)", content)
        self.assertIn('data-reference-url="/reference/item-types/"', content)
        self.assertIn('data-reference-url="/reference/market-groups/"', content)
        self.assertIn('data-reference-url="/reference/solar-systems/"', content)
        self.assertEqual(content.count('role="combobox"'), 3)
        self.assertNotIn("Alliance ID", content)
        self.assertNotIn("Item type IDs", content)
        self.assertNotIn("solar system names or IDs", content)

    def test_historical_scans_are_not_rendered(self):
        other_user = get_user_model().objects.create_user(
            username=f"other-audit-user-{get_user_model().objects.count()}"
        )
        run = AssetAuditRun.objects.create(
            requested_by=other_user,
            target_type_ids=[34],
            target_solar_system_ids=[30000142],
        )

        response = self.client.get("/", {"run": run.pk})
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(f"Scan #{run.pk}", content)
        self.assertNotIn("Recent scans", content)

    def test_character_list_shows_portrait_and_expiry(self):
        snapshot = CharacterAssetSnapshot(
            character_id=123,
            owner_hash="owner-hash",
            assets=[],
            locations={},
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
            etag='"version-1"',
        )
        character = {
            "character_id": 123,
            "character_name": "Example Pilot",
            "portrait_url": "https://images.evetech.net/characters/123/portrait?size=64",
            "snapshot": snapshot,
            "expires_at": snapshot.expires_at,
            "freshness_class": "text-success",
            "freshness_label": "Fresh",
            "has_asset_scope": True,
            "refreshable": False,
        }
        with patch(
            "asset_audit.views.get_character_statuses",
            return_value=[character],
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Example Pilot", content)
        self.assertIn('src="https://images.evetech.net/characters/123/portrait?size=64"', content)
        self.assertIn("text-success", content)
        self.assertIn("Fresh", content)

    def test_character_statuses_include_owned_characters_without_asset_scope(self):
        CharacterAssetSnapshot.objects.update_or_create(
            character_id=1000,
            defaults={
                "owner_hash": "owner-fresh",
                "assets": [],
                "locations": {},
                "fetched_at": timezone.now(),
                "expires_at": timezone.now() + timezone.timedelta(hours=1),
                "etag": '"fresh"',
            },
        )
        ownership_queryset = Mock()
        ownership_queryset.values_list.return_value = [
            ("1000", "Fresh Pilot", 99000001, "Fresh Corp", "owner-fresh"),
            ("1001", "No Scope Pilot", 99000002, "Other Corp", "owner-no-scope"),
        ]
        ownership_model = Mock()
        ownership_model.objects.filter.return_value = ownership_queryset
        token_queryset = Mock()
        asset_scope_queryset = Mock()
        asset_scope_queryset.values_list.return_value = [(1000, "owner-fresh")]
        no_scope_queryset = Mock()
        no_scope_queryset.values_list.return_value = []
        token_queryset.require_scopes.side_effect = lambda scopes: (
            asset_scope_queryset
            if scopes == ["esi-assets.read_assets.v1"]
            else no_scope_queryset
        )
        token_model = Mock()
        token_model.objects.filter.return_value = token_queryset

        characters = self.get_character_statuses(ownership_model, token_model)

        self.assertEqual(
            [character["character_name"] for character in characters],
            ["Fresh Pilot", "No Scope Pilot"],
        )
        self.assertTrue(characters[0]["has_asset_scope"])
        self.assertFalse(characters[0]["refreshable"])
        self.assertFalse(characters[1]["has_asset_scope"])
        self.assertFalse(characters[1]["refreshable"])
        self.assertEqual(characters[1]["freshness_class"], "text-danger")
        self.assertFalse(characters[1]["has_roles_scope"])

    def test_character_enumeration_fetches_scoped_roles_and_marks_director(self):
        character_id = 91218390
        owner_hash = "director-owner"
        ownership_queryset = Mock()
        ownership_queryset.values_list.return_value = [
            (character_id, "Director Pilot", 99000090, "Example Corp", owner_hash)
        ]
        ownership_model = Mock()
        ownership_model.objects.filter.return_value = ownership_queryset

        scope_results = {
            "esi-assets.read_assets.v1": [],
            "esi-characters.read_corporation_roles.v1": [(character_id, owner_hash)],
            "esi-assets.read_corporation_assets.v1": [(character_id, owner_hash)],
            "esi-contracts.read_character_contracts.v1": [],
            "esi-markets.read_character_orders.v1": [],
            "esi-contracts.read_corporation_contracts.v1": [],
            "esi-markets.read_corporation_orders.v1": [],
        }
        token_queryset = Mock()
        token_queryset.require_scopes.side_effect = lambda scopes: Mock(
            values_list=Mock(return_value=scope_results[scopes[0]])
        )
        token_model = Mock()
        token_model.objects.filter.return_value = token_queryset
        role_snapshot = CharacterRoleSnapshot(
            character_id=character_id,
            owner_hash=owner_hash,
            corporation_id=99000090,
            roles=["Director"],
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
            etag='"roles-1"',
        )

        with patch(
            "asset_audit.views.refresh_character_role_snapshot",
            return_value=(role_snapshot, False),
        ) as refresh_roles:
            characters = self.get_character_statuses(ownership_model, token_model)

        refresh_roles.assert_called_once_with(character_id, 99000090, owner_hash)
        self.assertTrue(characters[0]["is_director"])
        self.assertTrue(characters[0]["has_roles_scope"])
        self.assertTrue(characters[0]["has_corporation_asset_scope"])

    def test_director_corporation_is_rendered_with_cached_expiry_and_logo(self):
        corporation_id = 99000091
        CorporationAssetSnapshot.objects.update_or_create(
            corporation_id=corporation_id,
            defaults={
                "director_character_id": 91218391,
                "owner_hash": "director-owner",
                "assets": [],
                "locations": {},
                "fetched_at": timezone.now(),
                "expires_at": timezone.now() + timezone.timedelta(hours=1),
                "etag": '"corp-assets-1"',
            },
        )[0]
        character = {
            "character_id": 91218391,
            "character_name": "Director Pilot",
            "corporation_id": corporation_id,
            "corporation_name": "Example Corporation",
            "owner_hash": "director-owner",
            "is_director": True,
            "has_corporation_asset_scope": True,
        }
        second_director = {
            **character,
            "character_id": 91218393,
            "character_name": "Second Director",
            "has_corporation_asset_scope": False,
        }

        with patch(
            "asset_audit.views.get_character_statuses",
            return_value=[character, second_director],
        ):
            response = self.client.get("/")

        content = response.content.decode()
        self.assertIn("Example Corporation", content)
        self.assertIn(
            f'src="https://images.evetech.net/corporations/{corporation_id}/logo?size=64"',
            content,
        )
        self.assertIn("Assets ready", content)
        self.assertIn('data-expiry datetime="', content)
        self.assertEqual(
            content.count(f'data-corporation-id="{corporation_id}"'),
            1,
        )

    def test_corporation_status_uses_one_director_with_broadest_endpoint_access(self):
        from asset_audit.views import get_corporation_statuses

        corporation_id = 99000098
        directors = [
            {
                "character_id": 200,
                "corporation_id": corporation_id,
                "corporation_name": "Scope Corporation",
                "owner_hash": "asset-director",
                "is_director": True,
                "has_corporation_asset_scope": True,
                "has_corporation_contract_scope": False,
                "has_corporation_order_scope": False,
            },
            {
                "character_id": 100,
                "corporation_id": corporation_id,
                "corporation_name": "Scope Corporation",
                "owner_hash": "all-endpoints-director",
                "is_director": True,
                "has_corporation_asset_scope": True,
                "has_corporation_contract_scope": True,
                "has_corporation_order_scope": True,
            },
        ]

        corporation = get_corporation_statuses(directors)[0]

        self.assertEqual(corporation["director_character_id"], 100)
        self.assertEqual(corporation["owner_hash"], "all-endpoints-director")
        self.assertTrue(corporation["has_asset_scope"])
        self.assertTrue(corporation["has_contract_scope"])
        self.assertTrue(corporation["has_order_scope"])

    def test_corporation_assets_are_included_in_local_search(self):
        InventoryTypeReference.objects.update_or_create(
            id=29991,
            defaults={"name": "Corporation Loki"},
        )
        location = ResolvedLocation(30000142, "Jita", 30000142, "Jita")
        snapshot = CorporationAssetSnapshot(
            corporation_id=99000092,
            director_character_id=91218392,
            owner_hash="director-owner",
            assets=[
                {
                    "item_id": 1055831652213,
                    "type_id": 29991,
                    "location_id": 30000142,
                    "location_type": "solar_system",
                    "quantity": 1,
                    "is_singleton": True,
                }
            ],
            locations={
                "30000142": {
                    "location_id": location.location_id,
                    "name": location.name,
                    "solar_system_id": location.solar_system_id,
                    "solar_system_name": location.solar_system_name,
                }
            },
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        corporation = {
            "corporation_id": snapshot.corporation_id,
            "corporation_name": "Example Corporation",
            "logo_url": "https://images.evetech.net/corporations/99000092/logo?size=64",
            "snapshot": snapshot,
            "has_asset_scope": True,
        }

        with patch(
            "asset_audit.views.lookup_inventory_type_ids_for_market_groups",
            return_value=[],
        ):
            matches = self._search_corporation_assets(corporation)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["audit_owner_type"], "corporation")
        self.assertEqual(matches[0]["audit_owner_name"], "Example Corporation")
        from asset_audit.views import _build_match_groups

        groups = _build_match_groups(matches, [])
        self.assertEqual(
            groups[0]["matches"][0]["owner_name"],
            "Example Corporation",
        )

    def test_search_returns_typed_assets_contracts_and_market_orders(self):
        from asset_audit.views import _build_match_groups, _search_cached_assets

        type_id = 29992
        system_id = 30000142
        InventoryTypeReference.objects.update_or_create(
            id=type_id,
            defaults={"name": "Test Item"},
        )
        location = {
            str(system_id): {
                "location_id": system_id,
                "name": "Jita",
                "solar_system_id": system_id,
                "solar_system_name": "Jita",
            }
        }
        character = {
            "character_id": 123,
            "character_name": "Contract Pilot",
            "owner_hash": "character-owner",
            "has_asset_scope": True,
            "has_contract_scope": True,
            "has_order_scope": False,
            "snapshot": CharacterAssetSnapshot(
                assets=[
                    {
                        "item_id": 1001,
                        "type_id": type_id,
                        "location_id": system_id,
                        "is_singleton": True,
                        "quantity": 1,
                    },
                    {
                        "item_id": 1002,
                        "type_id": type_id,
                        "location_id": system_id,
                        "is_singleton": False,
                        "quantity": 10,
                    },
                ],
                locations=location,
            ),
            "contracts_snapshot": CharacterContractSnapshot(
                contracts=[
                    {
                        "contract_id": 5001,
                        "type": "item_exchange",
                        "status": "outstanding",
                        "title": "Test contract",
                        "start_location_id": system_id,
                    },
                    {
                        "contract_id": 5002,
                        "type": "item_exchange",
                        "status": "finished",
                        "title": "Finished contract",
                        "start_location_id": system_id,
                    },
                    {
                        "contract_id": 5003,
                        "type": "item_exchange",
                        "status": "finished_issuer",
                        "title": "Finished issuer contract",
                        "start_location_id": system_id,
                    },
                ],
                locations=location,
            ),
            "contract_item_snapshots": [
                ContractItemSnapshot(
                    owner_type=ContractItemSnapshot.OwnerType.CHARACTER,
                    owner_id=123,
                    owner_hash="character-owner",
                    contract_id=5001,
                    items=[
                        {
                            "record_id": 2001,
                            "type_id": type_id,
                            "quantity": 2,
                            "is_singleton": False,
                            "is_included": True,
                        },
                        {
                            "record_id": 2002,
                            "type_id": type_id,
                            "quantity": 3,
                            "is_singleton": False,
                            "is_included": False,
                        },
                    ],
                ),
                ContractItemSnapshot(
                    owner_type=ContractItemSnapshot.OwnerType.CHARACTER,
                    owner_id=123,
                    owner_hash="character-owner",
                    contract_id=5002,
                    items=[{"type_id": type_id, "is_included": True}],
                ),
                ContractItemSnapshot(
                    owner_type=ContractItemSnapshot.OwnerType.CHARACTER,
                    owner_id=123,
                    owner_hash="character-owner",
                    contract_id=5003,
                    items=[{"type_id": type_id, "is_included": True}],
                ),
            ],
            "orders_snapshot": None,
        }
        corporation = {
            "corporation_id": 99000201,
            "corporation_name": "Market Corporation",
            "logo_url": "https://images.evetech.net/corporations/99000201/logo?size=64",
            "director_character_id": 456,
            "owner_hash": "director-owner",
            "has_asset_scope": False,
            "has_contract_scope": True,
            "has_order_scope": True,
            "snapshot": None,
            "contracts_snapshot": CorporationContractSnapshot(
                contracts=[
                    {
                        "contract_id": 6001,
                        "type": "item_exchange",
                        "status": "finished_contractor",
                        "start_location_id": system_id,
                    }
                ],
                locations=location,
            ),
            "contract_item_snapshots": [
                ContractItemSnapshot(
                    owner_type=ContractItemSnapshot.OwnerType.CORPORATION,
                    owner_id=99000201,
                    owner_hash="director-owner",
                    director_character_id=456,
                    contract_id=6001,
                    items=[{"type_id": type_id, "is_included": True}],
                )
            ],
            "orders_snapshot": CorporationOrderSnapshot(
                orders=[
                    {
                        "order_id": 3001,
                        "type_id": type_id,
                        "location_id": system_id,
                        "volume_remain": 4,
                    },
                    {
                        "order_id": 3002,
                        "type_id": type_id,
                        "location_id": 30002659,
                        "volume_remain": 5,
                    },
                ],
                locations={
                    **location,
                    "30002659": {
                        "location_id": 30002659,
                        "name": "Ashab",
                        "solar_system_id": 30002659,
                        "solar_system_name": "Ashab",
                    },
                },
            ),
        }

        with patch(
            "asset_audit.views.lookup_inventory_type_ids_for_market_groups",
            return_value=[],
        ):
            matches = _search_cached_assets(
                [character],
                [type_id],
                [],
                [system_id],
                [corporation],
            )
        groups = _build_match_groups(matches, [character])

        self.assertEqual(len(matches), 4)
        self.assertEqual(
            {match["audit_result_type"] for match in matches},
            {"Assembled Asset", "Packaged Asset", "Contract", "Market"},
        )
        self.assertEqual(
            {match["owner_name"] for group in groups for match in group["matches"]},
            {"Contract Pilot", "Market Corporation"},
        )
        self.assertIn(
            "Contract 5001: Test contract",
            {match["reference"] for group in groups for match in group["matches"]},
        )
        references = {
            match["reference"] for group in groups for match in group["matches"]
        }
        self.assertFalse(
            references
            & {
                "Contract 5002: Finished contract",
                "Contract 5003: Finished issuer contract",
                "Contract 6001",
            }
        )

    def _search_corporation_assets(self, corporation):
        from asset_audit.views import _search_cached_assets

        return _search_cached_assets([], [29991], [], [30000142], [corporation])

    def test_refresh_endpoint_refreshes_each_character_endpoint(self):
        with (
            patch(
                "asset_audit.views.refresh_character_snapshot",
                return_value=(
                    CharacterAssetSnapshot(
                        character_id=123,
                        owner_hash="owner-hash",
                        fetched_at=timezone.now(),
                        expires_at=timezone.now() + timezone.timedelta(hours=1),
                        etag='"version-1"',
                    ),
                    False,
                ),
            ) as refresh_assets,
            patch(
                "asset_audit.views.refresh_character_contract_snapshot",
                return_value=(
                    CharacterContractSnapshot(
                        character_id=123,
                        owner_hash="owner-hash",
                        contracts=[],
                        fetched_at=timezone.now(),
                        expires_at=timezone.now() + timezone.timedelta(minutes=5),
                        etag='"contracts-1"',
                    ),
                    False,
                ),
            ) as refresh_contracts,
            patch(
                "asset_audit.views.refresh_character_order_snapshot",
                return_value=(
                    CharacterOrderSnapshot(
                        character_id=123,
                        owner_hash="owner-hash",
                        orders=[],
                        fetched_at=timezone.now(),
                        expires_at=timezone.now() + timezone.timedelta(minutes=20),
                        etag='"orders-1"',
                    ),
                    False,
                ),
            ) as refresh_orders,
        ):
            response = self.client.post("/refresh/123/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["character_id"], 123)
        self.assertEqual(data["etag"], '"version-1"')
        self.assertEqual(
            set(data["endpoints"]),
            {"assets", "contracts", "contract_items", "orders"},
        )
        self.assertEqual(data["endpoints"]["contracts"]["etag"], '"contracts-1"')
        self.assertEqual(data["endpoints"]["orders"]["etag"], '"orders-1"')
        refresh_assets.assert_called_once_with(123)
        refresh_contracts.assert_called_once_with(123)
        refresh_orders.assert_called_once_with(123)

    @patch("asset_audit.views.refresh_corporation_snapshot")
    @patch("asset_audit.views.refresh_corporation_contract_snapshot")
    @patch("asset_audit.views.refresh_corporation_order_snapshot")
    def test_corporation_refresh_endpoint_uses_director_credentials(
        self,
        refresh_orders,
        refresh_contracts,
        refresh_assets,
    ):
        corporation_id = 99000199
        character = {
            "character_id": 91218399,
            "character_name": "Director Pilot",
            "corporation_id": corporation_id,
            "corporation_name": "Example Corporation",
            "owner_hash": "director-owner",
            "is_director": True,
            "has_corporation_asset_scope": True,
        }
        refresh_assets.return_value = (
            CorporationAssetSnapshot(
                corporation_id=corporation_id,
                director_character_id=character["character_id"],
                owner_hash=character["owner_hash"],
                fetched_at=timezone.now(),
                expires_at=timezone.now() + timezone.timedelta(hours=1),
                etag='"corp-version-1"',
            ),
            False,
        )
        refresh_contracts.return_value = (
            CorporationContractSnapshot(
                corporation_id=corporation_id,
                director_character_id=character["character_id"],
                owner_hash=character["owner_hash"],
                contracts=[],
                fetched_at=timezone.now(),
                expires_at=timezone.now() + timezone.timedelta(minutes=5),
                etag='"corp-contracts-1"',
            ),
            False,
        )
        refresh_orders.return_value = (
            CorporationOrderSnapshot(
                corporation_id=corporation_id,
                director_character_id=character["character_id"],
                owner_hash=character["owner_hash"],
                orders=[],
                fetched_at=timezone.now(),
                expires_at=timezone.now() + timezone.timedelta(minutes=20),
                etag='"corp-orders-1"',
            ),
            False,
        )

        with patch(
            "asset_audit.views.get_character_statuses",
            return_value=[character],
        ):
            response = self.client.post(
                f"/refresh/corporation/{corporation_id}/"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["corporation_id"], corporation_id)
        self.assertEqual(response.json()["etag"], '"corp-version-1"')
        self.assertEqual(
            set(response.json()["endpoints"]),
            {"assets", "contracts", "contract_items", "orders"},
        )
        refresh_assets.assert_called_once_with(
            corporation_id,
            character["character_id"],
            character["owner_hash"],
        )
        refresh_contracts.assert_called_once_with(
            corporation_id,
            character["character_id"],
            character["owner_hash"],
        )
        refresh_orders.assert_called_once_with(
            corporation_id,
            character["character_id"],
            character["owner_hash"],
        )

    def test_user_without_audit_permission_sees_access_notice(self):
        member = get_user_model().objects.create_user(
            username=f"regular-member-{get_user_model().objects.count()}"
        )
        client = Client()
        client.force_login(member)

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            "By authenticating with this Alliance Auth server you are providing "
            "permission for Alliance Management and their authorised representatives "
            "to search and identify your assets and their locations.",
            content,
        )
        self.assertIn("https://developers.eveonline.com/authorized-apps", content)
        self.assertIn('the "Revoke Access" button', content)
        self.assertNotIn("Search assets", content)
        self.assertNotIn("Recent scans", content)

        response = client.get("/reference/solar-systems/", {"q": "ji"})
        self.assertEqual(response.status_code, 302)
        response = client.get("/reference/item-types/", {"q": "tr"})
        self.assertEqual(response.status_code, 302)
        response = client.get("/reference/market-groups/", {"q": "mi"})
        self.assertEqual(response.status_code, 302)

    @patch("asset_audit.views.search_solar_systems")
    def test_solar_system_reference_endpoint_returns_search_results(self, search):
        search.return_value = [{"id": 30000142, "name": "Jita"}]

        response = self.client.get("/reference/solar-systems/", {"q": "jit"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"results": [{"id": 30000142, "name": "Jita"}]},
        )
        search.assert_called_once_with("jit")

    @patch("asset_audit.views.search_solar_systems")
    def test_solar_system_reference_endpoint_skips_short_queries(self, search):
        response = self.client.get("/reference/solar-systems/", {"q": "j"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})
        search.assert_not_called()

    @patch("asset_audit.views.search_inventory_types")
    def test_item_type_reference_endpoint_returns_local_search_results(self, search):
        search.return_value = [
            {"id": 34, "name": "Tritanium", "market_group_id": 1857}
        ]

        response = self.client.get("/reference/item-types/", {"q": "tri"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    {"id": 34, "name": "Tritanium", "market_group_id": 1857}
                ]
            },
        )
        search.assert_called_once_with("tri")

    @patch("asset_audit.views.search_market_groups")
    def test_market_group_reference_endpoint_returns_local_search_results(self, search):
        search.return_value = [
            {
                "id": 1857,
                "name": "Minerals",
                "parent_group_id": 4,
                "label": "Minerals (Ship Equipment)",
            }
        ]

        response = self.client.get("/reference/market-groups/", {"q": "min"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    {
                        "id": 1857,
                        "name": "Minerals",
                        "parent_group_id": 4,
                        "label": "Minerals (Ship Equipment)",
                    }
                ]
            },
        )
        search.assert_called_once_with("min")

    def test_solar_system_reference_endpoint_searches_local_catalog(self):
        csv_contents = b'"solarSystemID","solarSystemName"\n"30000142","Jita"\n'
        with tempfile.TemporaryDirectory() as cache_directory:
            csv_path = Path(cache_directory) / "mapSolarSystems.csv"
            csv_path.write_bytes(csv_contents)
            (Path(cache_directory) / "mapSolarSystems.csv.md5sum").write_text(
                hashlib.md5(csv_contents, usedforsecurity=False).hexdigest(),
                encoding="ascii",
            )
            with override_settings(
                ASSET_AUDIT_REFERENCE_CACHE_DIR=cache_directory
            ):
                response = self.client.get(
                    "/reference/solar-systems/", {"q": "jit"}
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"results": [{"id": 30000142, "name": "Jita"}]},
        )


if __name__ == "__main__":
    unittest.main()