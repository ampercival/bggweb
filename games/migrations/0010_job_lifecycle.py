from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('games', '0009_ownedgame_unique')]
    operations = [
        migrations.AddField(model_name='fetchjob', name='heartbeat_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.CreateModel(name='CatalogLease', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('job', models.ForeignKey(to='games.fetchjob', null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
        ]),
    ]
