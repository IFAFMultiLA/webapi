# Generated by Django 4.2.3 on 2023-11-02 17:37

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0015_alter_userfeedback_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationsession",
            name="description",
            field=models.CharField(blank=True, default="", max_length=256, verbose_name="Description"),
        ),
    ]
