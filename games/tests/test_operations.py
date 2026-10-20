import sqlite3
import tempfile
from contextlib import closing
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from games.models import CatalogRevision, FetchJob, Game
from games.management.commands.run_worker import ReportingRunner
from .test_views import VIEW_TEST_SETTINGS


@override_settings(**VIEW_TEST_SETTINGS)
class HealthTests(TestCase):
    def test_web_health_and_worker_readiness_differ(self):
        self.assertEqual(self.client.get('/health/').status_code, 200)
        self.assertEqual(self.client.get('/health/worker/').status_code, 503)
        CatalogRevision.objects.create(pk=1, worker_seen_at=timezone.now())
        self.assertEqual(self.client.get('/health/worker/').status_code, 200)

    def test_busy_worker_heartbeat_counts(self):
        FetchJob.objects.create(kind='refresh', status='running', heartbeat_at=timezone.now())
        self.assertEqual(self.client.get('/health/worker/').status_code, 200)

    def test_queue_wrapper_reports_idle_worker(self):
        with patch('background_task.tasks.tasks.run_next_task', return_value=False):
            from background_task.tasks import tasks
            ReportingRunner(tasks).run_next_task()
        self.assertIsNotNone(CatalogRevision.objects.get(pk=1).worker_seen_at)


class BackupRestoreTests(TransactionTestCase):
    def test_backup_restores_into_disposable_database_without_changing_live_data(self):
        from django.db import connection
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite backup command; PostgreSQL uses pg_dump.')
        Game.objects.create(bgg_id='1', title='Before backup', type='Base Game')
        with tempfile.TemporaryDirectory(prefix='bggweb-restore-test-') as directory:
            backup = Path(directory) / 'backup.sqlite3'
            restored = Path(directory) / 'restored.sqlite3'
            call_command('backup_catalog', str(backup), stdout=StringIO())
            with self.assertRaises(CommandError):
                call_command('backup_catalog', str(backup), stdout=StringIO())
            Game.objects.filter(bgg_id='1').update(title='After backup')
            with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(restored)) as target:
                source.backup(target)
                self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(target.execute('SELECT title FROM games_game').fetchone()[0], 'Before backup')
            self.assertEqual(Game.objects.get(bgg_id='1').title, 'After backup')
