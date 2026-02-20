
import os
import django
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bggweb.settings")
django.setup()

from games.models import Game
from games.services.bgg_client import BGGClient
from games.tasks import _sync_catalog

def run():
    print("Fetching top 100 games to populate mechanics...")
    # Get top 100 by rank
    games = Game.objects.order_by('bgg_rank')[:100]
    game_ids = [g.bgg_id for g in games]
    
    print(f"Games to fetch: {len(game_ids)}")
    
    client = BGGClient()
    # Batch size 20
    details, counts = client.fetch_details_batches(game_ids, batch_size=20)
    
    print(f"Fetched {len(details)} details.")
    
    # We need a map for _sync_catalog
    games_map = {g.bgg_id: {'Game Title': g.title, 'Type': 'Base Game'} for g in games} # Simplified
    
    _sync_catalog(games_map, details, counts, prune=False)
    
    # Verify
    with_mechanics = Game.objects.filter(mechanics__isnull=False).count()
    print(f"Games with mechanics now: {with_mechanics}")

if __name__ == '__main__':
    run()
