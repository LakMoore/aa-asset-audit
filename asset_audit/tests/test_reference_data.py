import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _django_test_setup import setup_django

setup_django()

from django.test import override_settings

from asset_audit.models import (
    InventoryTypeReference,
    MarketGroupReference,
    ReferenceDatasetVersion,
    SolarSystemReference,
)
from asset_audit.reference_cache import REFERENCE_DATASETS
from asset_audit.reference_data import ensure_reference_dataset_loaded


class ReferenceDataLoadTests(unittest.TestCase):
    def test_each_cached_csv_loads_only_when_its_md5_changes(self):
        contents = {
            "mapSolarSystems.csv": (
                b'"solarSystemID","solarSystemName"\n"30000142","Jita"\n'
            ),
            "invTypes.csv": (
                b'"typeID","typeName","marketGroupID"\n"34","Tritanium","1857"\n'
            ),
            "invMarketGroups.csv": (
                b'"marketGroupID","marketGroupName","parentGroupID"\n'
                b'"1857","Minerals","4"\n'
            ),
        }
        with tempfile.TemporaryDirectory() as cache_directory:
            for dataset in REFERENCE_DATASETS:
                csv_contents = contents[dataset.filename]
                (Path(cache_directory) / dataset.filename).write_bytes(csv_contents)
                (Path(cache_directory) / f"{dataset.filename}.md5sum").write_text(
                    hashlib.md5(csv_contents, usedforsecurity=False).hexdigest(),
                    encoding="ascii",
                )

            with override_settings(
                ASSET_AUDIT_REFERENCE_CACHE_DIR=cache_directory
            ):
                self.assertTrue(ensure_reference_dataset_loaded("solar_systems"))
                self.assertTrue(ensure_reference_dataset_loaded("item_types"))
                self.assertTrue(ensure_reference_dataset_loaded("market_groups"))
                with patch(
                    "asset_audit.reference_data._file_md5",
                    side_effect=AssertionError("unchanged data should not be hashed"),
                ):
                    self.assertFalse(
                        ensure_reference_dataset_loaded("solar_systems")
                    )
                (Path(cache_directory) / "mapSolarSystems.csv").unlink()
                (Path(cache_directory) / "mapSolarSystems.csv.md5sum").unlink()
                self.assertFalse(ensure_reference_dataset_loaded("solar_systems"))

        self.assertEqual(
            list(SolarSystemReference.objects.values_list("id", "name")),
            [(30000142, "Jita")],
        )
        self.assertEqual(
            list(
                InventoryTypeReference.objects.values_list(
                    "id", "name", "market_group_id"
                )
            ),
            [(34, "Tritanium", 1857)],
        )
        self.assertEqual(
            list(
                MarketGroupReference.objects.values_list(
                    "id", "name", "parent_group_id"
                )
            ),
            [(1857, "Minerals", 4)],
        )
        self.assertEqual(ReferenceDatasetVersion.objects.count(), 3)


if __name__ == "__main__":
    unittest.main()