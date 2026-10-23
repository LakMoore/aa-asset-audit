from allianceauth import hooks
from allianceauth.menu.hooks import MenuItemHook
from allianceauth.services.hooks import UrlHook

from asset_audit import urls


@hooks.register("url_hook")
def register_urls():
    return UrlHook(
        urls,
        "asset_audit",
        r"^asset-audit/",
        excluded_views=[
            "asset_audit.views.index",
            "asset_audit.views.refresh_character_assets",
            "asset_audit.views.refresh_corporation_assets",
            "asset_audit.views.solar_system_reference",
        ],
    )


@hooks.register("menu_item_hook")
def register_menu():
    return MenuItemHook(
        "Asset Audit",
        "fas fa-search fa-fw",
        "asset_audit:index",
        1500,
    )