# Generated by Django 4.2.14 on 2024-08-08 15:00

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0022_alter_applicationsessiongate_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationsession",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="Is active"),
        ),
    ]
