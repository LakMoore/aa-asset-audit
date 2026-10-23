from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("asset_audit", "0006_character_name_reference"),
    ]

    operations = [
        migrations.AddField(
            model_name="assetauditrun",
            name="target_market_group_ids",
            field=models.JSONField(default=list),
        ),
    ]