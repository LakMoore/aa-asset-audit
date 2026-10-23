from django.urls import path

from asset_audit import views

app_name = "asset_audit"

urlpatterns = [
    path("", views.index, name="index"),
    path(
        "refresh/<int:character_id>/",
        views.refresh_character_assets,
        name="refresh_character_assets",
    ),
    path(
        "refresh/corporation/<int:corporation_id>/",
        views.refresh_corporation_assets,
        name="refresh_corporation_assets",
    ),
    path(
        "reference/solar-systems/",
        views.solar_system_reference,
        name="solar_system_reference",
    ),
    path(
        "reference/item-types/",
        views.item_type_reference,
        name="item_type_reference",
    ),
    path(
        "reference/market-groups/",
        views.market_group_reference,
        name="market_group_reference",
    ),
]