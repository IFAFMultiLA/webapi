# Generated by Django 4.2.3 on 2023-07-26 09:22

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0010_alter_application_options_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="userfeedback",
            name="application_session",
        ),
        migrations.AddField(
            model_name="userfeedback",
            name="user_app_session",
            field=models.ForeignKey(
                default=1, on_delete=django.db.models.deletion.CASCADE, to="api.userapplicationsession"
            ),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="userfeedback",
            constraint=models.UniqueConstraint(
                fields=("user_app_session", "content_section"), name="unique_userappsess_content_section"
            ),
        ),
    ]
