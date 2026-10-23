import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from _django_test_setup import setup_django

setup_django()

from django.test import override_settings

from asset_audit.reference_cache import (
    REFERENCE_DATASETS,
    cached_csv_path,
    cached_md5_path,
    poll_reference_updates,
)


class ReferenceCacheTests(unittest.TestCase):
    def test_downloads_changed_files_and_skips_unchanged_versions(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            payloads = {
                dataset.filename: f"id,name\n1,{dataset.key}\n".encode()
                for dataset in REFERENCE_DATASETS
            }
            headers = {
                dataset.url: {
                    "ETag": f'"{dataset.key}-v1"',
                    "Last-Modified": "Mon, 12 Oct 2026 13:37:00 GMT",
                    "Content-Length": str(len(payloads[dataset.filename])),
                }
                for dataset in REFERENCE_DATASETS
            }

            def open_url(request, timeout):
                if request.get_method() == "HEAD":
                    response = BytesIO()
                    response.headers = headers[request.full_url]
                    return response
                return BytesIO(payloads[Path(request.full_url).name])

            with (
                override_settings(
                    ASSET_AUDIT_REFERENCE_CACHE_DIR=temporary_directory
                ),
                patch("asset_audit.reference_cache.urlopen", side_effect=open_url) as open_mock,
            ):
                first_poll = poll_reference_updates()
                second_poll = poll_reference_updates()

                self.assertEqual(
                    first_poll,
                    {dataset.key: True for dataset in REFERENCE_DATASETS},
                )
                self.assertEqual(
                    second_poll,
                    {dataset.key: False for dataset in REFERENCE_DATASETS},
                )

                changed_dataset = REFERENCE_DATASETS[0]
                payloads[changed_dataset.filename] = b"id,name\n2,updated\n"
                headers[changed_dataset.url]["ETag"] = '"solar-systems-v2"'
                headers[changed_dataset.url]["Content-Length"] = str(
                    len(payloads[changed_dataset.filename])
                )
                third_poll = poll_reference_updates()

                self.assertEqual(
                    third_poll,
                    {
                        dataset.key: dataset == changed_dataset
                        for dataset in REFERENCE_DATASETS
                    },
                )
                self.assertEqual(
                    sum(
                        call.args[0].get_method() == "GET"
                        for call in open_mock.call_args_list
                    ),
                    len(REFERENCE_DATASETS) + 1,
                )
                for dataset in REFERENCE_DATASETS:
                    self.assertEqual(
                        cached_csv_path(dataset).read_bytes(),
                        payloads[dataset.filename],
                    )
                    self.assertEqual(
                        cached_md5_path(dataset).read_text().strip(),
                        hashlib.md5(
                            payloads[dataset.filename], usedforsecurity=False
                        ).hexdigest(),
                    )


if __name__ == "__main__":
    unittest.main()