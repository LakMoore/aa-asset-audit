from importlib import import_module
import logging

from django.apps import AppConfig
from django.conf import settings


logger = logging.getLogger(__name__)


class AssetAuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "asset_audit"
    label = "asset_audit"
    verbose_name = "Asset Audit"

    def ready(self):
        import_module("asset_audit.checks")
        if not getattr(settings, "ASSET_AUDIT_POLL_REFERENCE_DATA_ON_STARTUP", True):
            return

        from asset_audit.reference_cache import poll_reference_updates

        try:
            poll_reference_updates()
        except Exception:
            logger.warning(
                "Could not poll Fuzzworks reference data during startup.",
                exc_info=True,
            )