from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0012_catalog_revision')]
    operations = [migrations.AddField(model_name='catalogrevision', name='worker_seen_at', field=models.DateTimeField(null=True, blank=True))]
