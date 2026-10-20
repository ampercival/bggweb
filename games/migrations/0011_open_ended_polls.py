from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0010_job_lifecycle')]
    operations = [
        migrations.AddField(model_name='playercountrecommendation', name='is_open_ended', field=models.BooleanField(default=False)),
        migrations.AlterUniqueTogether(name='playercountrecommendation', unique_together={('game', 'count', 'is_open_ended')}),
    ]
