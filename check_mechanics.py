
import os
import django
from django.db.models import Count

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bggweb.settings")
django.setup()

from games.models import Game

def run():
    total = Game.objects.count()
    with_mechanics = Game.objects.filter(mechanics__isnull=False).distinct().count()
    
    print(f"Total Games: {total}")
    print(f"Games with Mechanics: {with_mechanics}")
    
    if with_mechanics > 0:
        print("Sample games with mechanics:")
        for g in Game.objects.filter(mechanics__isnull=False).distinct()[:5]:
            print(f"- {g.title} ({g.bgg_id}): {list(g.mechanics.values_list('name', flat=True))}")

if __name__ == '__main__':
    run()
