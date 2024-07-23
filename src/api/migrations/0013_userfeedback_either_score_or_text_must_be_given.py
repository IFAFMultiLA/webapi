# Generated by Django 4.2.3 on 2023-07-26 11:41

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0012_userfeedback_created"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="userfeedback",
            constraint=models.CheckConstraint(
                check=models.Q(("score__isnull", False), ("text__isnull", False), _connector="OR"),
                name="either_score_or_text_must_be_given",
            ),
        ),
    ]
