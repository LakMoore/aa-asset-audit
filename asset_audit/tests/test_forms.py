import unittest
from unittest.mock import patch

from _django_test_setup import setup_django

setup_django()

from asset_audit.forms import AssetAuditForm
from asset_audit.models import MarketGroupReference
from asset_audit.reference import (
    lookup_market_group_ids,
    market_group_display_names,
    search_market_groups,
)


class AssetAuditFormTests(unittest.TestCase):
    def test_resolves_name_filters(self):
        with (
            patch(
                "asset_audit.forms.lookup_inventory_type_ids",
                return_value={"tritanium": [34, 36], "mexallon": [35]},
            ),
            patch(
                "asset_audit.forms.lookup_market_group_ids",
                return_value={"minerals": [1857], "modules": [1858]},
            ),
            patch(
                "asset_audit.forms.lookup_solar_system_ids",
                return_value={"jita": [30000142]},
            ),
        ):
            form = AssetAuditForm(
                {
                    "target_type_names": "Tritanium, Mexallon, Tritanium",
                    "target_market_group_names": "Minerals, Modules, Minerals",
                    "target_solar_system_names": "Jita",
                }
            )
            is_valid = form.is_valid()

        self.assertTrue(is_valid, form.errors)
        self.assertEqual(form.cleaned_data["target_type_names"], [34, 36, 35])
        self.assertEqual(form.cleaned_data["target_market_group_names"], [1857, 1858])
        self.assertEqual(form.cleaned_data["target_solar_system_names"], [30000142])

    def test_rejects_numeric_ids_in_all_name_fields(self):
        form = AssetAuditForm(
            {
                "target_type_names": "34",
                "target_market_group_names": "1857",
                "target_solar_system_names": "30000142",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("names, not IDs", str(form.errors))

    def test_rejects_unknown_type_and_system_names(self):
        with (
            patch("asset_audit.forms.lookup_inventory_type_ids", return_value={}),
            patch("asset_audit.forms.lookup_market_group_ids", return_value={}),
            patch("asset_audit.forms.lookup_solar_system_ids", return_value={}),
        ):
            form = AssetAuditForm(
                {
                    "target_type_names": "Not A Type",
                    "target_market_group_names": "Not A Group",
                    "target_solar_system_names": "Not A System",
                }
            )
            is_valid = form.is_valid()

        self.assertFalse(is_valid)
        self.assertIn("Unknown item type name(s): Not A Type", str(form.errors))
        self.assertIn("Unknown market group name(s): Not A Group", str(form.errors))
        self.assertIn("Unknown solar system name(s): Not A System", str(form.errors))

    def test_rejects_empty_filters(self):
        form = AssetAuditForm(
            {
                "target_type_names": "",
                "target_market_group_names": "",
                "target_solar_system_names": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Select at least one item type or market group", str(form.errors))
        self.assertIn("target_solar_system_names", form.errors)

    def test_market_group_alone_is_a_valid_item_filter(self):
        with (
            patch("asset_audit.forms.lookup_market_group_ids", return_value={"ships": [4]}),
            patch("asset_audit.forms.lookup_solar_system_ids", return_value={"jita": [30]}),
        ):
            form = AssetAuditForm(
                {
                    "target_type_names": "",
                    "target_market_group_names": "Ships",
                    "target_solar_system_names": "Jita",
                }
            )
            is_valid = form.is_valid()

        self.assertTrue(is_valid, form.errors)
        self.assertEqual(form.cleaned_data["target_type_names"], [])
        self.assertEqual(form.cleaned_data["target_market_group_names"], [4])

    def test_duplicate_market_group_labels_use_unique_parent_names(self):
        groups = [
            (99001001, "Ships", None),
            (99001002, "Blueprints & Reactions", None),
            (99001003, "Ship SKINs", None),
            (99001011, "Capital Ships", 99001001),
            (99001012, "Ships", 99001002),
            (99001013, "Capital Ships", 99001003),
            (99001021, "Freighters", 99001011),
            (99001022, "Freighters", 99001012),
            (99001023, "Freighters", 99001013),
        ]
        for group_id, name, parent_group_id in groups:
            MarketGroupReference.objects.update_or_create(
                id=group_id,
                defaults={"name": name, "parent_group_id": parent_group_id},
            )

        with patch("asset_audit.reference.ensure_reference_dataset_loaded"):
            labels = market_group_display_names([99001021, 99001022, 99001023])
            resolved = lookup_market_group_ids(list(labels.values()))
            suggestions = search_market_groups("Freighters")

        self.assertEqual(
            labels,
            {
                99001021: "Freighters (Capital Ships)",
                99001022: "Freighters (Blueprints)",
                99001023: "Freighters (Ship SKINs)",
            },
        )
        self.assertEqual(
            resolved,
            {
                "freighters (capital ships)": [99001021],
                "freighters (blueprints)": [99001022],
                "freighters (ship skins)": [99001023],
            },
        )
        self.assertEqual(
            {
                result["id"]: result["label"]
                for result in suggestions
                if result["name"] == "Freighters"
            },
            labels,
        )

        with (
            patch("asset_audit.reference.ensure_reference_dataset_loaded"),
            patch(
                "asset_audit.forms.lookup_solar_system_ids",
                return_value={"jita": [30000142]},
            ),
        ):
            form = AssetAuditForm(
                {
                    "target_type_names": "",
                    "target_market_group_names": labels[99001021],
                    "target_solar_system_names": "Jita",
                }
            )
            is_valid = form.is_valid()

        self.assertTrue(is_valid, form.errors)
        self.assertEqual(form.cleaned_data["target_market_group_names"], [99001021])

    def test_resolves_legacy_hierarchical_market_group_label(self):
        groups = [
            (99001101, "Ships", None),
            (99001102, "Blueprints & Reactions", None),
            (99001103, "Ship SKINs", None),
            (99001111, "Capital Ships", 99001101),
            (99001112, "Ships", 99001102),
            (99001113, "Capital Ships", 99001103),
            (99001121, "Legacy Freighters", 99001111),
            (99001122, "Legacy Freighters", 99001112),
            (99001123, "Legacy Freighters", 99001113),
        ]
        for group_id, name, parent_group_id in groups:
            MarketGroupReference.objects.update_or_create(
                id=group_id,
                defaults={"name": name, "parent_group_id": parent_group_id},
            )

        with patch("asset_audit.reference.ensure_reference_dataset_loaded"):
            resolved = lookup_market_group_ids(
                ["Legacy Freighters (Capital Ships / Ships)"]
            )

        self.assertEqual(
            resolved,
            {"legacy freighters (capital ships / ships)": [99001121]},
        )


if __name__ == "__main__":
    unittest.main()