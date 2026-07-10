import logging

from django.apps import AppConfig
from django.db.backends.signals import connection_created

log = logging.getLogger(__name__)


class GamesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'games'

    def ready(self):
        # Enable better SQLite concurrency by switching to WAL journaling.
        def _set_sqlite_pragmas(sender, connection, **kwargs):
            if connection.vendor == 'sqlite':
                try:
                    cursor = connection.cursor()
                    cursor.execute('PRAGMA journal_mode=WAL;')
                    cursor.execute('PRAGMA synchronous=NORMAL;')
                except Exception:
                    log.warning('Could not apply SQLite WAL pragmas', exc_info=True)
        connection_created.connect(_set_sqlite_pragmas)
