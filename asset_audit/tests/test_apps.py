import unittest
from importlib import import_module
from unittest.mock import patch

from _django_test_setup import setup_django

setup_django()

from django.test import override_settings

from asset_audit.apps import AssetAuditConfig


class AssetAuditStartupTests(unittest.TestCase):
    @override_settings(ASSET_AUDIT_POLL_REFERENCE_DATA_ON_STARTUP=True)
    @patch("asset_audit.reference_cache.poll_reference_updates")
    def test_ready_polls_reference_data(self, poll):
        config = AssetAuditConfig("asset_audit", import_module("asset_audit"))

        config.ready()

        poll.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()