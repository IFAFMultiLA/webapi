# Generated by Django 4.1.7 on 2023-03-20 13:06

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0002_alter_applicationconfig_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationsession",
            name="updated",
            field=models.DateTimeField(auto_now=True, verbose_name="Last update"),
        ),
    ]
