from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0002_solarsystemreference"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryTypeReference",
            fields=[
                ("id", models.PositiveIntegerField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("market_group_id", models.PositiveIntegerField(blank=True, null=True)),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.CreateModel(
            name="MarketGroupReference",
            fields=[
                ("id", models.PositiveIntegerField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("parent_group_id", models.PositiveIntegerField(blank=True, null=True)),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.CreateModel(
            name="ReferenceDatasetVersion",
            fields=[
                ("dataset", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("md5sum", models.CharField(max_length=32)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]