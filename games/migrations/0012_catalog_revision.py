from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0011_open_ended_polls')]
    operations = [migrations.CreateModel(name='CatalogRevision', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('version', models.PositiveBigIntegerField(default=0)),
        ('source_updated', models.JSONField(default=dict)),
    ])]
