# Generated by Django 4.2.15 on 2024-09-10 15:58

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0024_applicationsessiongate_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='local_appdir',
            field=models.CharField(default=None, max_length=512, null=True, verbose_name='Local app deployment directory'),
        ),
    ]
