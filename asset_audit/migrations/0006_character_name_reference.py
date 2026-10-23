from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0005_asset_location_references"),
    ]

    operations = [
        migrations.CreateModel(
            name="CharacterNameReference",
            fields=[
                (
                    "character_id",
                    models.PositiveBigIntegerField(primary_key=True, serialize=False),
                ),
                ("name", models.CharField(max_length=254)),
            ],
        ),
    ]