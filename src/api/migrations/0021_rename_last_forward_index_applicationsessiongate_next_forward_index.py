# Generated by Django 4.2.8 on 2023-12-12 08:23

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0020_applicationsessiongate_unique_label'),
    ]

    operations = [
        migrations.RenameField(
            model_name='applicationsessiongate',
            old_name='last_forward_index',
            new_name='next_forward_index',
        ),
    ]
