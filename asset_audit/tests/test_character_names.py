import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from _django_test_setup import setup_django

setup_django()

from asset_audit.character_names import resolve_character_names
from asset_audit.models import CharacterNameReference


class CharacterNameResolutionTests(unittest.TestCase):
    def test_uses_allianceauth_then_cache_then_caches_esi_names(self):
        CharacterNameReference.objects.create(
            character_id=202,
            name="Cached Character",
        )
        allianceauth_queryset = Mock()
        allianceauth_queryset.values_list.return_value = [
            (101, "Alliance Auth Character")
        ]
        allianceauth_model = SimpleNamespace(
            objects=SimpleNamespace(
                filter=Mock(return_value=allianceauth_queryset)
            )
        )
        esi_lookup = Mock(return_value={303: "ESI Character"})

        names = resolve_character_names(
            [303, 101, 202],
            allianceauth_character_model=allianceauth_model,
            esi_lookup=esi_lookup,
        )
        cached_names = resolve_character_names(
            [303, 101, 202],
            allianceauth_character_model=allianceauth_model,
            esi_lookup=esi_lookup,
        )

        self.assertEqual(
            names,
            {
                101: "Alliance Auth Character",
                202: "Cached Character",
                303: "ESI Character",
            },
        )
        self.assertEqual(cached_names, names)
        esi_lookup.assert_called_once_with([303])
        self.assertTrue(
            CharacterNameReference.objects.filter(
                character_id=303,
                name="ESI Character",
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()