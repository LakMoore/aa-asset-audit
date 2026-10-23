import logging
from collections.abc import Callable
from typing import Any

from asset_audit.esi import fetch_character_names
from asset_audit.models import CharacterNameReference

logger = logging.getLogger(__name__)


def resolve_character_names(
    character_ids,
    *,
    allianceauth_character_model: Any = None,
    esi_lookup: Callable[[list[int]], dict[int, str]] | None = None,
) -> dict[int, str]:
    character_ids = sorted({int(character_id) for character_id in character_ids})
    if not character_ids:
        return {}

    names = {}
    if allianceauth_character_model is None:
        try:
            from allianceauth.eveonline.models import EveCharacter

            allianceauth_character_model = EveCharacter
        except (ImportError, RuntimeError):
            allianceauth_character_model = None

    if allianceauth_character_model is not None:
        names.update(
            dict(
                allianceauth_character_model.objects.filter(
                    character_id__in=character_ids
                ).values_list("character_id", "character_name")
            )
        )

    unresolved_ids = set(character_ids) - set(names)
    cached_names = dict(
        CharacterNameReference.objects.filter(
            character_id__in=unresolved_ids
        ).exclude(name="").values_list("character_id", "name")
    )
    names.update(cached_names)

    unresolved_ids -= set(cached_names)
    if unresolved_ids:
        esi_lookup = esi_lookup or fetch_character_names
        try:
            esi_names = esi_lookup(sorted(unresolved_ids))
        except Exception:
            logger.warning(
                "Could not resolve names for %d character(s) from ESI.",
                len(unresolved_ids),
                exc_info=True,
            )
            esi_names = {}

        for character_id, name in esi_names.items():
            if not name:
                continue
            character_id = int(character_id)
            CharacterNameReference.objects.update_or_create(
                character_id=character_id,
                defaults={"name": name},
            )
            names[character_id] = name

    return names