import sqlite3
from contextlib import closing
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Create and verify a consistent SQLite backup at a NEW destination path.'

    def add_arguments(self, parser):
        parser.add_argument('destination')

    def handle(self, destination, **options):
        if connection.vendor != 'sqlite':
            raise CommandError('For PostgreSQL use pg_dump and the restore procedure in docs/OPERATIONS.md.')
        target = Path(destination).resolve()
        if target.exists():
            raise CommandError('Destination already exists; choose a new backup filename.')
        target.parent.mkdir(parents=True, exist_ok=True)
        connection.ensure_connection()
        try:
            with closing(sqlite3.connect(target)) as backup:
                connection.connection.backup(backup)
                result = backup.execute('PRAGMA integrity_check').fetchone()[0]
                if result != 'ok':
                    raise CommandError('Backup integrity check failed.')
                count = backup.execute('SELECT COUNT(*) FROM games_game').fetchone()[0]
        except Exception as exc:
            raise CommandError(f'Backup failed; inspect the incomplete file at {target}.') from exc
        self.stdout.write(f'Verified backup: {target} ({count} games).')
