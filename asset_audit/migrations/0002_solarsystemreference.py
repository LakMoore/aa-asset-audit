from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SolarSystemReference",
            fields=[
                ("id", models.PositiveIntegerField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
            ],
            options={
                "ordering": ["name", "id"],
            },
        ),
    ]