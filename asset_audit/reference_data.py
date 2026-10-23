import csv
import hashlib
from itertools import islice
from pathlib import Path

from django.db import transaction

from asset_audit.models import (
    InventoryTypeReference,
    MarketGroupReference,
    ReferenceDatasetVersion,
    SolarSystemReference,
)
from asset_audit.reference_cache import (
    REFERENCE_DATASETS,
    cached_csv_path,
    cached_md5_path,
)


class ReferenceDataUnavailable(Exception):
    """Raised when a valid cached dataset is not available to load."""


_MODELS = {
    "solar_systems": SolarSystemReference,
    "item_types": InventoryTypeReference,
    "market_groups": MarketGroupReference,
}
_REQUIRED_COLUMNS = {
    "solar_systems": {"solarSystemID", "solarSystemName"},
    "item_types": {"typeID", "typeName", "marketGroupID"},
    "market_groups": {"marketGroupID", "marketGroupName", "parentGroupID"},
}
_BATCH_SIZE = 1000


def _file_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as csv_file:
        for chunk in iter(lambda: csv_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nullable_id(value: str) -> int | None:
    return int(value) if value else None


def _make_record(dataset: str, row: dict[str, str]):
    if dataset == "solar_systems":
        return SolarSystemReference(
            id=int(row["solarSystemID"]),
            name=row["solarSystemName"],
        )
    if dataset == "item_types":
        return InventoryTypeReference(
            id=int(row["typeID"]),
            name=row["typeName"],
            market_group_id=_nullable_id(row["marketGroupID"]),
        )
    return MarketGroupReference(
        id=int(row["marketGroupID"]),
        name=row["marketGroupName"],
        parent_group_id=_nullable_id(row["parentGroupID"]),
    )


def _record_batches(dataset: str, csv_path: Path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or not _REQUIRED_COLUMNS[dataset].issubset(
            reader.fieldnames
        ):
            raise ReferenceDataUnavailable(
                f"Unexpected columns in cached {dataset} CSV."
            )
        while rows := list(islice(reader, _BATCH_SIZE)):
            try:
                yield [_make_record(dataset, row) for row in rows]
            except (TypeError, ValueError, KeyError) as error:
                raise ReferenceDataUnavailable(
                    f"Invalid row in cached {dataset} CSV."
                ) from error


def ensure_reference_dataset_loaded(dataset: str) -> bool:
    """Load a cached CSV only when its MD5 differs from the database version."""
    if dataset not in _MODELS:
        raise ValueError(f"Unknown reference dataset: {dataset}")
    source = next(item for item in REFERENCE_DATASETS if item.key == dataset)
    csv_path = cached_csv_path(source)
    md5_path = cached_md5_path(source)
    try:
        loaded_version = ReferenceDatasetVersion.objects.filter(
            dataset=dataset
        ).first()
    except Exception as error:
        raise ReferenceDataUnavailable(
            f"Could not inspect the loaded version for {dataset}."
        ) from error

    try:
        expected_md5 = md5_path.read_text(encoding="ascii").strip()
    except OSError as error:
        if loaded_version:
            return False
        raise ReferenceDataUnavailable(
            f"Cached MD5 for {dataset} is not available."
        ) from error
    if loaded_version and expected_md5 == loaded_version.md5sum:
        return False
    if not csv_path.is_file():
        if loaded_version:
            return False
        raise ReferenceDataUnavailable(
            f"Cached CSV for {dataset} is not available."
        )
    actual_md5 = _file_md5(csv_path)
    if not expected_md5 or actual_md5 != expected_md5:
        if loaded_version:
            return False
        raise ReferenceDataUnavailable(
            f"Cached CSV for {dataset} failed its MD5 check."
        )

    try:
        if loaded_version and loaded_version.md5sum == actual_md5:
            return False

        model = _MODELS[dataset]
        with transaction.atomic():
            model.objects.all().delete()
            row_count = 0
            for batch in _record_batches(dataset, csv_path):
                model.objects.bulk_create(batch, batch_size=_BATCH_SIZE)
                row_count += len(batch)
            if not row_count:
                raise ReferenceDataUnavailable(
                    f"Cached CSV for {dataset} contains no records."
                )
            ReferenceDatasetVersion.objects.update_or_create(
                dataset=dataset,
                defaults={"md5sum": actual_md5},
            )
    except ReferenceDataUnavailable:
        raise
    except Exception as error:
        raise ReferenceDataUnavailable(
            f"Could not load cached data for {dataset}."
        ) from error
    return True