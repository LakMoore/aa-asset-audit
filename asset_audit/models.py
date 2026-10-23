from django.conf import settings
from django.db import models
from django.utils import timezone


class SolarSystemReference(models.Model):
    id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ["name", "id"]


class InventoryTypeReference(models.Model):
    id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    market_group_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["name", "id"]


class MarketGroupReference(models.Model):
    id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    parent_group_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["name", "id"]


class ReferenceDatasetVersion(models.Model):
    dataset = models.CharField(max_length=32, primary_key=True)
    md5sum = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)


class CharacterAssetSnapshot(models.Model):
    character_id = models.PositiveBigIntegerField(primary_key=True)
    owner_hash = models.CharField(max_length=28)
    assets = models.JSONField(default=list)
    locations = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")


class CharacterRoleSnapshot(models.Model):
    character_id = models.PositiveBigIntegerField(primary_key=True)
    owner_hash = models.CharField(max_length=28)
    corporation_id = models.PositiveBigIntegerField()
    roles = models.JSONField(default=list)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")


class CorporationAssetSnapshot(models.Model):
    corporation_id = models.PositiveBigIntegerField(primary_key=True)
    director_character_id = models.PositiveBigIntegerField()
    owner_hash = models.CharField(max_length=28)
    assets = models.JSONField(default=list)
    locations = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")


class CharacterContractSnapshot(models.Model):
    character_id = models.PositiveBigIntegerField(primary_key=True)
    owner_hash = models.CharField(max_length=28)
    contracts = models.JSONField(default=list)
    locations = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")


class CharacterOrderSnapshot(models.Model):
    character_id = models.PositiveBigIntegerField(primary_key=True)
    owner_hash = models.CharField(max_length=28)
    orders = models.JSONField(default=list)
    locations = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")


class CorporationContractSnapshot(models.Model):
    corporation_id = models.PositiveBigIntegerField(primary_key=True)
    director_character_id = models.PositiveBigIntegerField()
    owner_hash = models.CharField(max_length=28)
    contracts = models.JSONField(default=list)
    locations = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")


class CorporationOrderSnapshot(models.Model):
    corporation_id = models.PositiveBigIntegerField(primary_key=True)
    director_character_id = models.PositiveBigIntegerField()
    owner_hash = models.CharField(max_length=28)
    orders = models.JSONField(default=list)
    locations = models.JSONField(default=dict)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")


class ContractItemSnapshot(models.Model):
    class OwnerType(models.TextChoices):
        CHARACTER = "character", "Character"
        CORPORATION = "corporation", "Corporation"

    owner_type = models.CharField(max_length=16, choices=OwnerType.choices)
    owner_id = models.PositiveBigIntegerField()
    owner_hash = models.CharField(max_length=28)
    director_character_id = models.PositiveBigIntegerField(null=True, blank=True)
    contract_id = models.PositiveBigIntegerField()
    items = models.JSONField(default=list)
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_type", "owner_id", "contract_id"],
                name="aa_contract_items_owner_contract_uniq",
            )
        ]


class RecentAssetAuditSearch(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="asset_audit_recent_searches",
    )
    strategy_key = models.CharField(max_length=64)
    query = models.JSONField(default=dict)
    last_used_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_used_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "strategy_key"],
                name="aa_recent_search_user_key_uniq",
            )
        ]


class StationReference(models.Model):
    station_id = models.PositiveBigIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    solar_system_id = models.PositiveIntegerField()


class StructureReference(models.Model):
    structure_id = models.PositiveBigIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    solar_system_id = models.PositiveIntegerField()


class CharacterNameReference(models.Model):
    character_id = models.PositiveBigIntegerField(primary_key=True)
    name = models.CharField(max_length=254)


class AssetAuditRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETE = "complete", "Complete"
        COMPLETE_WITH_ERRORS = "complete_with_errors", "Complete with errors"
        FAILED = "failed", "Failed"

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="asset_audit_runs",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )
    target_type_ids = models.JSONField(default=list)
    target_market_group_ids = models.JSONField(default=list)
    target_solar_system_ids = models.JSONField(default=list)
    character_count = models.PositiveIntegerField(default=0)
    completed_character_count = models.PositiveIntegerField(default=0)
    failed_character_count = models.PositiveIntegerField(default=0)
    processed_character_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("run_alliance_asset_audit", "Can run server-wide asset audits"),
        ]


class AssetAuditMatch(models.Model):
    run = models.ForeignKey(
        AssetAuditRun,
        on_delete=models.CASCADE,
        related_name="matches",
    )
    character_id = models.PositiveBigIntegerField()
    item_id = models.PositiveBigIntegerField()
    type_id = models.PositiveIntegerField()
    location_id = models.PositiveBigIntegerField()
    location_name = models.CharField(max_length=255, blank=True, default="")
    solar_system_id = models.PositiveIntegerField(null=True, blank=True)
    solar_system_name = models.CharField(max_length=255, blank=True, default="")
    location_type = models.CharField(max_length=32, blank=True)
    location_flag = models.CharField(max_length=64, blank=True)
    quantity = models.BigIntegerField(default=1)
    is_singleton = models.BooleanField(default=False)

    class Meta:
        ordering = ["character_id", "type_id", "item_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "item_id"],
                name="asset_audit_unique_match_per_run",
            )
        ]