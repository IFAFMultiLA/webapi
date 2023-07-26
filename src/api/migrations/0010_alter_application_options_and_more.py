# Generated by Django 4.2.3 on 2023-07-26 08:50

import api.models
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0009_application_default_application_session'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='application',
            options={'ordering': ['name']},
        ),
        migrations.AlterField(
            model_name='applicationconfig',
            name='config',
            field=models.JSONField(blank=True, default=api.models.application_config_default_json_instance, verbose_name='Configuration'),
        ),
        migrations.CreateModel(
            name='UserFeedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content_section', models.CharField(max_length=1024, verbose_name='Content section identifier')),
                ('score', models.SmallIntegerField(default=None, null=True, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(5)], verbose_name='Feedback score')),
                ('text', models.TextField(default=None, null=True, verbose_name='Feedback text')),
                ('application_session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='api.applicationsession')),
                ('tracking_session', models.ForeignKey(default=None, null=True, on_delete=django.db.models.deletion.CASCADE, to='api.trackingsession')),
            ],
        ),
    ]
