from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from background_task.models import Task
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone

from games.jobs import JobCancelled, managed_job
from games.models import CatalogLease, FetchJob
from games.tasks import run_fetch_top_n
from games.services.bgg_client import BGGClient
from .test_bgg_client import FakeResp
from .test_views import VIEW_TEST_SETTINGS


@override_settings(**VIEW_TEST_SETTINGS)
class JobLifecycleTests(TestCase):
    def test_cancel_before_start_never_fetches(self):
        job = FetchJob.objects.create(kind='top_n', status='cancelling')
        with patch('games.tasks.BGGClient') as client:
            run_fetch_top_n.now(job.pk, 1, 'https://example.invalid/ranks.zip')
            client.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')

    def test_duplicate_and_missing_deliveries_are_noops(self):
        for status in ['running', 'done', 'error', 'cancelled']:
            job = FetchJob.objects.create(kind='top_n', status=status)
            with patch('games.tasks.BGGClient') as client:
                run_fetch_top_n.now(job.pk, 1)
                client.assert_not_called()
        run_fetch_top_n.now(999999, 1)

    def test_competing_writer_waits_without_claiming_job(self):
        first = FetchJob.objects.create(kind='top_n', status='running')
        second = FetchJob.objects.create(kind='top_n')
        CatalogLease.objects.create(pk=1, job=first)
        with self.assertRaisesRegex(RuntimeError, 'Another catalog'):
            run_fetch_top_n.now(second.pk, 1)
        second.refresh_from_db()
        self.assertEqual(second.status, 'pending')

    def test_clear_history_preserves_active_jobs_and_queue(self):
        user = get_user_model().objects.create_superuser('admin', '', 'test')
        self.client.force_login(user)
        job = FetchJob.objects.create(kind='top_n')
        run_fetch_top_n(job.pk, 1)
        self.client.post('/jobs/clear/')
        self.assertTrue(FetchJob.objects.filter(pk=job.pk).exists())
        self.assertTrue(Task.objects.exists())

    def test_stale_recovery_leaves_healthy_lease_alone(self):
        stale = FetchJob.objects.create(kind='top_n', status='running',
                                        heartbeat_at=timezone.now() - timedelta(minutes=20))
        healthy = FetchJob.objects.create(kind='top_n', status='running', heartbeat_at=timezone.now())
        CatalogLease.objects.create(pk=1, job=stale)
        call_command('recover_jobs', stdout=StringIO())
        stale.refresh_from_db()
        healthy.refresh_from_db()
        self.assertEqual(stale.status, 'error')
        self.assertEqual(healthy.status, 'running')
        self.assertIsNone(CatalogLease.objects.get(pk=1).job_id)

    def test_lease_released_on_unexpected_failure(self):
        job = FetchJob.objects.create(kind='top_n')

        @managed_job
        def fail(job_id):
            raise RuntimeError('unexpected failure')

        with self.assertRaises(RuntimeError):
            fail(job.pk)
        self.assertIsNone(CatalogLease.objects.get(pk=1).job_id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'error')

    def test_public_job_has_no_cancel_control(self):
        job = FetchJob.objects.create(kind='bga')
        response = self.client.get(f'/jobs/{job.pk}/')
        self.assertNotContains(response, 'id="cancelJobForm"')
        self.assertContains(response, 'Board Game Arena')

    def test_real_queue_delivery_runs_managed_job(self):
        from background_task.tasks import tasks
        from games.tasks import run_fetch_bga
        job = FetchJob.objects.create(kind='bga')
        run_fetch_bga(job.pk)
        with patch('games.tasks.BGGClient') as client:
            client.return_value.fetch_family_members.return_value = [{'bgg_id': '123', 'title': 'Fixture'}]
            self.assertTrue(tasks.run_next_task())
        job.refresh_from_db()
        self.assertEqual(job.status, 'done')
        self.assertFalse(Task.objects.exists())
        self.assertIsNone(CatalogLease.objects.get(pk=1).job_id)


class BoundedRetryTests(SimpleTestCase):
    def test_queued_collection_has_finite_attempts(self):
        client = BGGClient(throttle_sec=0)
        with patch.object(client, '_get', return_value=FakeResp(status_code=202)) as get, \
                patch.object(client, '_sleep'):
            with self.assertRaisesRegex(RuntimeError, '12 attempts'):
                client.fetch_owned_collection('alice')
        self.assertEqual(get.call_count, 12)

    def test_retry_wait_honors_cancellation(self):
        client = BGGClient(throttle_sec=0)
        client.check_cancelled = lambda: (_ for _ in ()).throw(JobCancelled())
        with self.assertRaises(JobCancelled):
            client._sleep(60)
