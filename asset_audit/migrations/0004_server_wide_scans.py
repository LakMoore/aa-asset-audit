from django.db import migrations


def update_audit_permission_name(apps, schema_editor):
    permission_model = apps.get_model("auth", "Permission")
    permission_model.objects.using(schema_editor.connection.alias).filter(
        content_type__app_label="asset_audit",
        content_type__model="assetauditrun",
        codename="run_alliance_asset_audit",
    ).update(name="Can run server-wide asset audits")


def restore_audit_permission_name(apps, schema_editor):
    permission_model = apps.get_model("auth", "Permission")
    permission_model.objects.using(schema_editor.connection.alias).filter(
        content_type__app_label="asset_audit",
        content_type__model="assetauditrun",
        codename="run_alliance_asset_audit",
    ).update(name="Can run Alliance-wide asset audits")


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0003_reference_datasets"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="assetauditrun",
            name="alliance_id",
        ),
        migrations.AlterModelOptions(
            name="assetauditrun",
            options={
                "ordering": ["-created_at"],
                "permissions": [
                    ("run_alliance_asset_audit", "Can run server-wide asset audits"),
                ],
            },
        ),
        migrations.RunPython(
            update_audit_permission_name,
            restore_audit_permission_name,
        ),
    ]