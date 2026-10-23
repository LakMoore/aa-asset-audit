import logging
from collections.abc import Iterator
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from asset_audit.esi import ASSET_READ_SCOPE, AssetTokenNotFound, fetch_character_assets
from asset_audit.matching import matching_assets
from asset_audit.location_resolution import resolve_asset_locations
from asset_audit.reference import lookup_inventory_type_ids_for_market_groups

logger = logging.getLogger(__name__)


def get_authenticated_asset_characters(
    ownership_model: Any = None,
    token_model: Any = None,
) -> Iterator[tuple[int, str]]:
    """Yield currently owned characters with an asset-scoped token."""
    if ownership_model is None or token_model is None:
        from allianceauth.authentication.models import CharacterOwnership
        from esi.models import Token

        ownership_model = ownership_model or CharacterOwnership
        token_model = token_model or Token

    active_owners = ownership_model.objects.filter(
        user__is_active=True,
    ).values_list("character__character_id", "owner_hash")
    owners = [(int(character_id), owner_hash) for character_id, owner_hash in active_owners]
    if not owners:
        return

    tokens = (
        token_model.objects.filter(
            character_id__in=[character_id for character_id, _ in owners],
            character_owner_hash__in=[owner_hash for _, owner_hash in owners],
        )
        .require_scopes([ASSET_READ_SCOPE])
        .values_list("character_id", "character_owner_hash")
    )
    eligible_owners = set(tokens)
    yield from (
        (character_id, owner_hash)
        for character_id, owner_hash in owners
        if (character_id, owner_hash) in eligible_owners
    )


@shared_task
def scan_character_assets(
    run_id: int,
    character_id: int,
    owner_hash: str,
) -> int:
    from asset_audit.models import AssetAuditMatch, AssetAuditRun

    run = AssetAuditRun.objects.get(pk=run_id)
    if run.status != AssetAuditRun.Status.RUNNING:
        return 0

    matches = []
    failed = False
    try:
        assets = fetch_character_assets(character_id, owner_hash=owner_hash)
        locations_by_id, unresolved_location_ids = resolve_asset_locations(
            assets,
            character_id,
            owner_hash,
        )
        target_type_ids = set(run.target_type_ids)
        target_type_ids.update(
            lookup_inventory_type_ids_for_market_groups(
                run.target_market_group_ids
            )
        )
        matches = matching_assets(
            assets,
            target_type_ids,
            run.target_solar_system_ids,
            locations_by_id,
        )
        if unresolved_location_ids:
            logger.warning(
                "Could not resolve %d asset location(s) for character %s.",
                len(unresolved_location_ids),
                character_id,
            )
            failed = True
    except AssetTokenNotFound:
        logger.warning("No current asset-read token for character %s", character_id)
        failed = True
    except Exception:
        logger.exception("Asset scan failed for character %s", character_id)
        failed = True

    match_records = [
        AssetAuditMatch(
            run_id=run_id,
            character_id=character_id,
            item_id=asset["item_id"],
            type_id=asset["type_id"],
            location_id=asset.get("resolved_location_id", asset["location_id"]),
            location_name=asset.get("resolved_location_name", ""),
            solar_system_id=asset.get("resolved_solar_system_id"),
            solar_system_name=asset.get("resolved_solar_system_name", ""),
            location_type=asset.get("location_type", ""),
            location_flag=asset.get("location_flag", ""),
            quantity=asset.get("quantity", 1),
            is_singleton=asset.get("is_singleton", False),
        )
        for asset in matches
    ]
    _record_character_scan(
        run_id,
        character_id,
        match_records,
        failed=failed,
    )
    logger.info("Asset scan found %d matches for character %s", len(matches), character_id)
    return len(matches)


def _record_character_scan(
    run_id: int,
    character_id: int,
    match_records: list[Any],
    *,
    failed: bool,
) -> None:
    from asset_audit.models import AssetAuditMatch, AssetAuditRun

    with transaction.atomic():
        run = AssetAuditRun.objects.select_for_update().get(pk=run_id)
        if (
            run.status != AssetAuditRun.Status.RUNNING
            or character_id in run.processed_character_ids
        ):
            return

        AssetAuditMatch.objects.bulk_create(match_records, ignore_conflicts=True)
        run.processed_character_ids.append(character_id)
        run.completed_character_count += 1
        run.failed_character_count += int(failed)
        if run.completed_character_count >= run.character_count:
            run.status = (
                AssetAuditRun.Status.COMPLETE_WITH_ERRORS
                if run.failed_character_count
                else AssetAuditRun.Status.COMPLETE
            )
            run.completed_at = timezone.now()
        run.save(
            update_fields=[
                "processed_character_ids",
                "completed_character_count",
                "failed_character_count",
                "status",
                "completed_at",
            ]
        )


@shared_task
def start_asset_audit(
    run_id: int,
) -> int:
    from asset_audit.models import AssetAuditRun

    run = AssetAuditRun.objects.get(pk=run_id)
    if run.status != AssetAuditRun.Status.PENDING:
        return 0

    characters = list(get_authenticated_asset_characters())
    run.character_count = len(characters)
    run.status = (
        AssetAuditRun.Status.RUNNING
        if characters
        else AssetAuditRun.Status.COMPLETE
    )
    if not characters:
        run.completed_at = timezone.now()
    run.save(
        update_fields=["character_count", "status", "completed_at"]
    )

    scheduled_count = 0
    try:
        for character_id, owner_hash in characters:
            scan_character_assets.delay(run.pk, character_id, owner_hash)
            scheduled_count += 1
    except Exception:
        logger.exception("Failed to schedule all character scans for audit %s", run_id)
        AssetAuditRun.objects.filter(pk=run_id).update(
            status=AssetAuditRun.Status.FAILED,
            completed_at=timezone.now(),
        )

    logger.info("Scheduled %d character scans for audit %s", scheduled_count, run_id)
    return scheduled_count