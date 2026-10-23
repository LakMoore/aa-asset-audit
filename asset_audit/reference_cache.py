import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)

FUZZWORKS_CSV_URL = "https://www.fuzzwork.co.uk/dump/latest/csv"
HEAD_TIMEOUT_SECONDS = 5
DOWNLOAD_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ReferenceDataset:
    key: str
    filename: str

    @property
    def url(self) -> str:
        return f"{FUZZWORKS_CSV_URL}/{self.filename}"


REFERENCE_DATASETS = (
    ReferenceDataset("solar_systems", "mapSolarSystems.csv"),
    ReferenceDataset("item_types", "invTypes.csv"),
    ReferenceDataset("market_groups", "invMarketGroups.csv"),
)


def cache_directory() -> Path:
    configured_path = getattr(settings, "ASSET_AUDIT_REFERENCE_CACHE_DIR", None)
    if configured_path:
        return Path(configured_path)
    return Path(settings.BASE_DIR) / ".cache" / "asset_audit"


def cached_csv_path(dataset: ReferenceDataset) -> Path:
    return cache_directory() / dataset.filename


def cached_md5_path(dataset: ReferenceDataset) -> Path:
    return cache_directory() / f"{dataset.filename}.md5sum"


def _metadata_path(dataset: ReferenceDataset) -> Path:
    return cache_directory() / f"{dataset.filename}.remote.json"


def _md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as csv_file:
        for chunk in iter(lambda: csv_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, str] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _remote_metadata(dataset: ReferenceDataset) -> dict[str, str]:
    request = Request(dataset.url, method="HEAD")
    with urlopen(request, timeout=HEAD_TIMEOUT_SECONDS) as response:
        headers = response.headers
        metadata = {
            "etag": headers.get("ETag", ""),
            "last_modified": headers.get("Last-Modified", ""),
            "content_length": headers.get("Content-Length", ""),
        }
    if not metadata["etag"] and not metadata["last_modified"]:
        raise ValueError(f"No version metadata returned for {dataset.filename}.")
    return metadata


def _cache_is_current(
    dataset: ReferenceDataset,
    remote_metadata: dict[str, str],
) -> bool:
    csv_path = cached_csv_path(dataset)
    md5_path = cached_md5_path(dataset)
    try:
        expected_md5 = md5_path.read_text(encoding="ascii").strip()
    except OSError:
        return False
    if not expected_md5 or not csv_path.is_file():
        return False
    if _md5_file(csv_path) != expected_md5:
        return False
    return _read_json(_metadata_path(dataset)) == remote_metadata


def _atomic_write_text(path: Path, value: str, encoding: str) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding=encoding) as temporary_file:
            temporary_file.write(value)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _download_dataset(
    dataset: ReferenceDataset,
    remote_metadata: dict[str, str],
) -> None:
    directory = cache_directory()
    directory.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(dir=directory)
    temporary_path = Path(temporary_name)
    digest = hashlib.md5(usedforsecurity=False)
    byte_count = 0
    try:
        request = Request(dataset.url, method="GET")
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                for chunk in iter(lambda: response.read(64 * 1024), b""):
                    temporary_file.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)

        expected_size = remote_metadata["content_length"]
        if expected_size and byte_count != int(expected_size):
            raise ValueError(f"Incomplete download for {dataset.filename}.")

        os.replace(temporary_path, cached_csv_path(dataset))
        _atomic_write_text(
            cached_md5_path(dataset), f"{digest.hexdigest()}\n", "ascii"
        )
        _atomic_write_text(
            _metadata_path(dataset),
            json.dumps(remote_metadata, sort_keys=True),
            "utf-8",
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def poll_reference_updates() -> dict[str, bool]:
    """Download changed Fuzzworks CSVs, retaining valid local copies on errors."""
    cache_directory().mkdir(parents=True, exist_ok=True)
    updated = {}
    for dataset in REFERENCE_DATASETS:
        try:
            metadata = _remote_metadata(dataset)
            if _cache_is_current(dataset, metadata):
                updated[dataset.key] = False
                continue
            _download_dataset(dataset, metadata)
            updated[dataset.key] = True
        except Exception:
            logger.warning(
                "Could not update Asset Audit reference data %s; keeping its "
                "previous cache if available.",
                dataset.filename,
                exc_info=True,
            )
            updated[dataset.key] = False
    return updated