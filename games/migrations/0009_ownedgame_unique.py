from django.db import migrations, models
from django.db.models import Count, Min


def deduplicate(apps, schema_editor):
    owned = apps.get_model('games', 'OwnedGame').objects.using(schema_editor.connection.alias)
    duplicates = list(owned.values('collection_id', 'game_id').annotate(
        n=Count('id'), keep=Min('id')).filter(n__gt=1))
    for row in duplicates:
        owned.filter(collection_id=row['collection_id'], game_id=row['game_id']).exclude(
            pk=row['keep']).delete()


class Migration(migrations.Migration):
    dependencies = [('games', '0008_bgagame_alter_fetchjob_kind')]
    operations = [
        migrations.RunPython(deduplicate, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='ownedgame',
            constraint=models.UniqueConstraint(fields=('collection', 'game'), name='unique_collection_game'),
        ),
    ]
