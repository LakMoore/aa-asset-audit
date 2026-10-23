from django.conf import settings
from django.core.checks import Error, Tags, register

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


@register(Tags.compatibility)
def check_asset_audit_login_scope(app_configs, **kwargs):
    if ASSET_READ_SCOPE in getattr(settings, "LOGIN_TOKEN_SCOPES", []):
        return []

    return [
        Error(
            f"Asset Audit requires {ASSET_READ_SCOPE} in LOGIN_TOKEN_SCOPES.",
            hint=f'Add "{ASSET_READ_SCOPE}" to LOGIN_TOKEN_SCOPES so every user login requests asset access.',
            id="asset_audit.E001",
        )
    ]


@register(Tags.compatibility)
def check_asset_audit_structure_scope(app_configs, **kwargs):
    if STRUCTURE_READ_SCOPE in getattr(settings, "LOGIN_TOKEN_SCOPES", []):
        return []

    return [
        Error(
            f"Asset Audit requires {STRUCTURE_READ_SCOPE} in LOGIN_TOKEN_SCOPES.",
            hint=(
                f'Add "{STRUCTURE_READ_SCOPE}" to LOGIN_TOKEN_SCOPES and '
                "reauthorize characters to resolve structure locations."
            ),
            id="asset_audit.E002",
        )
    ]


@register(Tags.compatibility)
def check_asset_audit_character_roles_scope(app_configs, **kwargs):
    if CHARACTER_ROLES_SCOPE in getattr(settings, "LOGIN_TOKEN_SCOPES", []):
        return []

    return [
        Error(
            f"Asset Audit requires {CHARACTER_ROLES_SCOPE} in LOGIN_TOKEN_SCOPES.",
            hint=(
                f'Add "{CHARACTER_ROLES_SCOPE}" to LOGIN_TOKEN_SCOPES and '
                "reauthorize characters to discover corporation Directors."
            ),
            id="asset_audit.E003",
        )
    ]


@register(Tags.compatibility)
def check_asset_audit_corporation_asset_scope(app_configs, **kwargs):
    if CORPORATION_ASSET_READ_SCOPE in getattr(settings, "LOGIN_TOKEN_SCOPES", []):
        return []

    return [
        Error(
            f"Asset Audit requires {CORPORATION_ASSET_READ_SCOPE} in LOGIN_TOKEN_SCOPES.",
            hint=(
                f'Add "{CORPORATION_ASSET_READ_SCOPE}" to LOGIN_TOKEN_SCOPES and '
                "reauthorize Director characters to read corporation assets."
            ),
            id="asset_audit.E004",
        )
    ]


def _required_scope_error(scope, error_id, purpose):
    if scope in getattr(settings, "LOGIN_TOKEN_SCOPES", []):
        return []
    return [
        Error(
            f"Asset Audit requires {scope} in LOGIN_TOKEN_SCOPES.",
            hint=(
                f'Add "{scope}" to LOGIN_TOKEN_SCOPES and reauthorize '
                f"characters to {purpose}."
            ),
            id=error_id,
        )
    ]


@register(Tags.compatibility)
def check_asset_audit_character_contract_scope(app_configs, **kwargs):
    return _required_scope_error(
        CHARACTER_CONTRACTS_READ_SCOPE,
        "asset_audit.E005",
        "read character contracts",
    )


@register(Tags.compatibility)
def check_asset_audit_corporation_contract_scope(app_configs, **kwargs):
    return _required_scope_error(
        CORPORATION_CONTRACTS_READ_SCOPE,
        "asset_audit.E006",
        "read corporation contracts with Director tokens",
    )


@register(Tags.compatibility)
def check_asset_audit_character_order_scope(app_configs, **kwargs):
    return _required_scope_error(
        CHARACTER_ORDERS_READ_SCOPE,
        "asset_audit.E007",
        "read character market orders",
    )


@register(Tags.compatibility)
def check_asset_audit_corporation_order_scope(app_configs, **kwargs):
    return _required_scope_error(
        CORPORATION_ORDERS_READ_SCOPE,
        "asset_audit.E008",
        "read corporation market orders with Director tokens",
    )