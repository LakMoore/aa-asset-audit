from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("asset_audit", "0008_character_asset_snapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecentAssetAuditSearch",
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
                ("strategy_key", models.CharField(max_length=64)),
                ("query", models.JSONField(default=dict)),
                (
                    "last_used_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="asset_audit_recent_searches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-last_used_at", "-pk"],
            },
        ),
        migrations.AddConstraint(
            model_name="recentassetauditsearch",
            constraint=models.UniqueConstraint(
                fields=("user", "strategy_key"),
                name="aa_recent_search_user_key_uniq",
            ),
        ),
    ]