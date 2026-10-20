"""Measure import writes in a disposable SQLite database, never the catalog.

Run from the repository: python scripts/benchmark_import.py [game-count]
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'bggweb.settings'
os.environ['DJANGO_DEBUG'] = 'True'

from django.conf import settings  # noqa: E402


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    with tempfile.TemporaryDirectory(prefix='bggweb-benchmark-') as directory:
        settings.DATABASES = {'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(Path(directory) / 'benchmark.sqlite3'),
        }}
        settings.DEBUG = False
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connections
        from games.tasks import _sync_catalog
        from games.tests.factories import game_info, game_detail, player_count

        call_command('migrate', verbosity=0)
        games = {str(i): game_info(i, f'Benchmark {i}') for i in range(1, count + 1)}
        details = {gid: game_detail(categories=['Economic'], mechanics=['Trading']) for gid in games}
        polls = {gid: {str(n): player_count(best_votes=10) for n in range(2, 6)} for gid in games}
        try:
            for mode in ['initial', 'unchanged', 'changed']:
                if mode == 'changed':
                    for info in games.values():
                        info['Average Rating'] = 8.1
                    for counts in polls.values():
                        for poll in counts.values():
                            poll['Best Votes'] = 20
                start = time.perf_counter()
                _sync_catalog(games, details, polls, prune=True)
                print(f'{mode}: {count} games / {count * 4} polls in {time.perf_counter()-start:.3f}s', flush=True)
        finally:
            connections.close_all()


if __name__ == '__main__':
    main()
