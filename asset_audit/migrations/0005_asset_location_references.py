from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0004_server_wide_scans"),
    ]

    operations = [
        migrations.CreateModel(
            name="StationReference",
            fields=[
                ("station_id", models.PositiveBigIntegerField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("solar_system_id", models.PositiveIntegerField()),
            ],
        ),
        migrations.CreateModel(
            name="StructureReference",
            fields=[
                ("structure_id", models.PositiveBigIntegerField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("solar_system_id", models.PositiveIntegerField()),
            ],
        ),
        migrations.RemoveField(
            model_name="assetauditrun",
            name="solar_system_by_location_id",
        ),
        migrations.AddField(
            model_name="assetauditmatch",
            name="location_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="assetauditmatch",
            name="solar_system_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="assetauditmatch",
            name="solar_system_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]