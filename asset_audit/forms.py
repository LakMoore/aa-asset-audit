from django import forms
from django.core.exceptions import ValidationError

from asset_audit.reference import (
    ReferenceNameLookupError,
    lookup_inventory_type_ids,
    lookup_market_group_ids,
    lookup_solar_system_ids,
)


def _reference_names(value: str, label: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise ValidationError(f"Enter at least one {label} name.")
    if any(name.isdecimal() for name in names):
        raise ValidationError(f"Enter {label} names, not IDs.")
    return list(dict.fromkeys(names))


class AssetAuditForm(forms.Form):
    target_type_names = forms.CharField(
        label="Item type names",
        required=False,
        help_text="Type an item name to search; separate multiple names with commas.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "off",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "target_type_names-options",
            }
        ),
    )
    target_market_group_names = forms.CharField(
        label="Market group names",
        required=False,
        help_text="Type a market group to search; separate multiple names with commas.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "off",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "target_market_group_names-options",
            }
        ),
    )
    target_solar_system_names = forms.CharField(
        label="Solar system names",
        help_text="Type a system name to search; separate multiple names with commas.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "off",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "target_solar_system_names-options",
            }
        ),
    )
    def clean_target_type_names(self) -> list[int]:
        if not self.cleaned_data["target_type_names"].strip():
            return []
        names = _reference_names(
            self.cleaned_data["target_type_names"], "item type"
        )
        try:
            resolved = lookup_inventory_type_ids(names)
        except ReferenceNameLookupError as error:
            raise ValidationError(
                "Could not load item type names from local reference data."
            ) from error

        unknown_names = [name for name in names if name.casefold() not in resolved]
        if unknown_names:
            raise ValidationError(
                "Unknown item type name(s): %(names)s.",
                params={"names": ", ".join(unknown_names)},
            )
        return list(
            dict.fromkeys(
                type_id
                for name in names
                for type_id in resolved[name.casefold()]
            )
        )

    def clean_target_market_group_names(self) -> list[int]:
        if not self.cleaned_data["target_market_group_names"].strip():
            return []
        names = _reference_names(
            self.cleaned_data["target_market_group_names"], "market group"
        )
        try:
            resolved = lookup_market_group_ids(names)
        except ReferenceNameLookupError as error:
            raise ValidationError(
                "Could not load market group names from local reference data."
            ) from error

        unknown_names = [name for name in names if name.casefold() not in resolved]
        if unknown_names:
            raise ValidationError(
                "Unknown market group name(s): %(names)s.",
                params={"names": ", ".join(unknown_names)},
            )
        return list(
            dict.fromkeys(
                group_id
                for name in names
                for group_id in resolved[name.casefold()]
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("target_type_names") and not cleaned_data.get(
            "target_market_group_names"
        ):
            raise ValidationError("Select at least one item type or market group.")
        return cleaned_data

    def clean_target_solar_system_names(self) -> list[int]:
        names = _reference_names(
            self.cleaned_data["target_solar_system_names"], "solar system"
        )
        try:
            resolved = lookup_solar_system_ids(names)
        except ReferenceNameLookupError as error:
            raise ValidationError(
                "Could not load solar system names from local reference data."
            ) from error

        unknown_names = [name for name in names if name.casefold() not in resolved]
        if unknown_names:
            raise ValidationError(
                "Unknown solar system name(s): %(names)s.",
                params={"names": ", ".join(unknown_names)},
            )
        return list(
            dict.fromkeys(
                system_id
                for name in names
                for system_id in resolved[name.casefold()]
            )
        )
