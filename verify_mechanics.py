
import os
import django
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bggweb.settings")
django.setup()

from games.services.bgg_client import BGGClient
from games.tasks import _sync_catalog
from games.models import Game, Mechanic

def run():
    print("Fetching details for Game ID 84419 (The Estates)...")
    client = BGGClient()
    # The Estates: 249381? No, user used 84419. 
    # 84419 is "The City"? Or user might have just picked random.
    # Let's use a game known to have mechanics. 
    # 84419: "Fear" (2010)?
    # Let's use "Catan" (13) or "Gloomhaven" (174430).
    # 13 has "Dice Rolling", "Hexagon Grid", etc.
    gid = '13' 
    
    details, counts = client.fetch_details_batches([gid])
    
    print(f"Details fetched for {gid}:")
    mech = details[gid].get('Mechanics', [])
    print(f"Mechanics: {mech}")
    
    if not mech:
        print("ERROR: No mechanics found for Catan!")
        sys.exit(1)
        
    print("Syncing to DB...")
    # We need a basic games_map structure for _sync_catalog
    games_map = {gid: {'Game Title': 'Catan TEST', 'Type': 'Base Game'}}
    
    _sync_catalog(games_map, details, counts, prune=False)
    
    print("Checking DB...")
    game = Game.objects.get(bgg_id=gid)
    db_mechs = list(game.mechanics.values_list('name', flat=True))
    print(f"DB Mechanics: {db_mechs}")
    
    if set(mech) == set(db_mechs):
        print("SUCCESS: DB mechanics match fetched mechanics.")
    else:
        print("ERROR: Mismatch!")
        # It's possible _sync_catalog behavior differs?
        # Note: _sync_catalog calls _sync_games which sets the fields.
        # But wait, my manual _sync_catalog call used games_map which relies on 'Game Title' etc.
        # The mechanics are in details_map, which is passed.
        sys.exit(1)

if __name__ == '__main__':
    run()
