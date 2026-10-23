import unittest

from django.test import override_settings

from _django_test_setup import setup_django

setup_django()

from asset_audit.checks import (
    check_asset_audit_login_scope,
    check_asset_audit_character_contract_scope,
    check_asset_audit_character_order_scope,
    check_asset_audit_character_roles_scope,
    check_asset_audit_corporation_asset_scope,
    check_asset_audit_corporation_contract_scope,
    check_asset_audit_corporation_order_scope,
    check_asset_audit_structure_scope,
)
from asset_audit.esi import (
    ASSET_READ_SCOPE,
    CHARACTER_CONTRACTS_READ_SCOPE,
    CHARACTER_ORDERS_READ_SCOPE,
    CHARACTER_ROLES_SCOPE,
    CORPORATION_ASSET_READ_SCOPE,
    CORPORATION_CONTRACTS_READ_SCOPE,
    CORPORATION_ORDERS_READ_SCOPE,
    STRUCTURE_READ_SCOPE,
)


class AssetAuditLoginScopeCheckTests(unittest.TestCase):
    def test_errors_when_asset_scope_is_not_required_for_login(self):
        with override_settings(LOGIN_TOKEN_SCOPES=["publicData"]):
            errors = check_asset_audit_login_scope(None)

        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "asset_audit.E001")

    def test_passes_when_asset_scope_is_required_for_login(self):
        with override_settings(
            LOGIN_TOKEN_SCOPES=["publicData", ASSET_READ_SCOPE]
        ):
            errors = check_asset_audit_login_scope(None)

        self.assertEqual(errors, [])

    def test_errors_when_structure_scope_is_not_required_for_login(self):
        with override_settings(LOGIN_TOKEN_SCOPES=["publicData", ASSET_READ_SCOPE]):
            errors = check_asset_audit_structure_scope(None)

        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "asset_audit.E002")

    def test_passes_when_structure_scope_is_required_for_login(self):
        with override_settings(
            LOGIN_TOKEN_SCOPES=[
                "publicData",
                ASSET_READ_SCOPE,
                STRUCTURE_READ_SCOPE,
            ]
        ):
            errors = check_asset_audit_structure_scope(None)

        self.assertEqual(errors, [])

    def test_requires_character_roles_scope_for_login(self):
        with override_settings(LOGIN_TOKEN_SCOPES=["publicData"]):
            errors = check_asset_audit_character_roles_scope(None)
        self.assertEqual([error.id for error in errors], ["asset_audit.E003"])

        with override_settings(LOGIN_TOKEN_SCOPES=[CHARACTER_ROLES_SCOPE]):
            errors = check_asset_audit_character_roles_scope(None)
        self.assertEqual(errors, [])

    def test_requires_corporation_asset_scope_for_login(self):
        with override_settings(LOGIN_TOKEN_SCOPES=["publicData"]):
            errors = check_asset_audit_corporation_asset_scope(None)
        self.assertEqual([error.id for error in errors], ["asset_audit.E004"])

        with override_settings(LOGIN_TOKEN_SCOPES=[CORPORATION_ASSET_READ_SCOPE]):
            errors = check_asset_audit_corporation_asset_scope(None)
        self.assertEqual(errors, [])

    def test_requires_contract_and_market_order_scopes_for_login(self):
        checks = [
            check_asset_audit_character_contract_scope,
            check_asset_audit_corporation_contract_scope,
            check_asset_audit_character_order_scope,
            check_asset_audit_corporation_order_scope,
        ]
        with override_settings(LOGIN_TOKEN_SCOPES=["publicData"]):
            errors = [error for check in checks for error in check(None)]
        self.assertEqual(
            [error.id for error in errors],
            [
                "asset_audit.E005",
                "asset_audit.E006",
                "asset_audit.E007",
                "asset_audit.E008",
            ],
        )

        with override_settings(
            LOGIN_TOKEN_SCOPES=[
                CHARACTER_CONTRACTS_READ_SCOPE,
                CORPORATION_CONTRACTS_READ_SCOPE,
                CHARACTER_ORDERS_READ_SCOPE,
                CORPORATION_ORDERS_READ_SCOPE,
            ]
        ):
            errors = [error for check in checks for error in check(None)]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()