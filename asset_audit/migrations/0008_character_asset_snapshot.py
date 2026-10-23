from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0007_market_group_search"),
    ]

    operations = [
        migrations.CreateModel(
            name="CharacterAssetSnapshot",
            fields=[
                (
                    "character_id",
                    models.PositiveBigIntegerField(primary_key=True, serialize=False),
                ),
                ("owner_hash", models.CharField(max_length=28)),
                ("assets", models.JSONField(default=list)),
                ("locations", models.JSONField(default=dict)),
                ("fetched_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("etag", models.CharField(blank=True, default="", max_length=255)),
            ],
        ),
    ]