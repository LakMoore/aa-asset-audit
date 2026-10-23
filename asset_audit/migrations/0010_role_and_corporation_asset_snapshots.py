from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0009_recent_asset_audit_search"),
    ]

    operations = [
        migrations.CreateModel(
            name="CharacterRoleSnapshot",
            fields=[
                (
                    "character_id",
                    models.PositiveBigIntegerField(primary_key=True, serialize=False),
                ),
                ("owner_hash", models.CharField(max_length=28)),
                ("corporation_id", models.PositiveBigIntegerField()),
                ("roles", models.JSONField(default=list)),
                ("fetched_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("etag", models.CharField(blank=True, default="", max_length=255)),
            ],
        ),
        migrations.CreateModel(
            name="CorporationAssetSnapshot",
            fields=[
                (
                    "corporation_id",
                    models.PositiveBigIntegerField(primary_key=True, serialize=False),
                ),
                ("director_character_id", models.PositiveBigIntegerField()),
                ("owner_hash", models.CharField(max_length=28)),
                ("assets", models.JSONField(default=list)),
                ("locations", models.JSONField(default=dict)),
                ("fetched_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("etag", models.CharField(blank=True, default="", max_length=255)),
            ],
        ),
    ]