import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssetAuditRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("complete", "Complete"),
                            ("complete_with_errors", "Complete with errors"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=24,
                    ),
                ),
                ("alliance_id", models.CharField(max_length=254)),
                ("target_type_ids", models.JSONField(default=list)),
                ("target_solar_system_ids", models.JSONField(default=list)),
                ("solar_system_by_location_id", models.JSONField(default=dict)),
                ("character_count", models.PositiveIntegerField(default=0)),
                ("completed_character_count", models.PositiveIntegerField(default=0)),
                ("failed_character_count", models.PositiveIntegerField(default=0)),
                ("processed_character_ids", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="asset_audit_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "permissions": [
                    ("run_alliance_asset_audit", "Can run Alliance-wide asset audits"),
                ],
            },
        ),
        migrations.CreateModel(
            name="AssetAuditMatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("character_id", models.PositiveBigIntegerField()),
                ("item_id", models.PositiveBigIntegerField()),
                ("type_id", models.PositiveIntegerField()),
                ("location_id", models.PositiveBigIntegerField()),
                ("location_type", models.CharField(blank=True, max_length=32)),
                ("location_flag", models.CharField(blank=True, max_length=64)),
                ("quantity", models.BigIntegerField(default=1)),
                ("is_singleton", models.BooleanField(default=False)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matches",
                        to="asset_audit.assetauditrun",
                    ),
                ),
            ],
            options={"ordering": ["character_id", "type_id", "item_id"]},
        ),
        migrations.AddConstraint(
            model_name="assetauditmatch",
            constraint=models.UniqueConstraint(
                fields=("run", "item_id"),
                name="asset_audit_unique_match_per_run",
            ),
        ),
    ]