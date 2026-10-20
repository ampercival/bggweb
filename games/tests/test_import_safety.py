from unittest.mock import Mock, patch

import requests
from django.test import TestCase, SimpleTestCase

from games.models import Collection, FetchJob, Game, OwnedGame
from games.tasks import JobCancelled, _sync_catalog, run_fetch_top_n, run_refresh
from games.services.bgg_client import BGGClient
from games.services.rtt_client import RTTClient
from .factories import game_info, game_detail, player_count
from .test_bgg_client import FakeResp


class ImportSafetyTests(TestCase):
    def setUp(self):
        _sync_catalog({'1': game_info(1, 'Original')}, {'1': game_detail(year=2000)},
                      {'1': {'4': player_count(best_votes=10)}})
        self.game = Game.objects.get(bgg_id='1')
        self.collection = Collection.objects.create(username='alice')
        OwnedGame.objects.create(collection=self.collection, game=self.game)

    def test_apply_failure_rolls_back_prune_and_updates(self):
        Game.objects.create(bgg_id='2', title='Keep on failure', type='Base Game')
        with patch('games.tasks._sync_player_counts', side_effect=RuntimeError('apply failed')):
            with self.assertRaises(RuntimeError):
                _sync_catalog({'1': game_info(1, 'Changed')}, {'1': game_detail()},
                              {'1': {}}, prune=True)
        self.game.refresh_from_db()
        self.assertEqual(self.game.title, 'Original')
        self.assertTrue(Game.objects.filter(bgg_id='2').exists())
        self.assertEqual(self.game.player_counts.count(), 1)

    def test_top_n_keeps_collection_only_games(self):
        _sync_catalog({'2': game_info(2)}, {'2': game_detail()}, {'2': {}}, prune=True)
        self.assertTrue(Game.objects.filter(pk=self.game.pk).exists())

    def test_cancel_during_apply_rolls_back(self):
        with self.assertRaises(JobCancelled):
            _sync_catalog({'1': game_info(1, 'Changed')}, {'1': game_detail()},
                          {'1': {}}, check_cancelled=Mock(side_effect=[None, JobCancelled()]))
        self.game.refresh_from_db()
        self.assertEqual(self.game.title, 'Original')
        self.assertEqual(self.game.player_counts.count(), 1)

    def test_successful_refresh_applies_only_after_fetch_and_keeps_tags(self):
        from games.models import BGAGame
        from games.tasks import sync_platform_collections
        BGAGame.objects.create(bgg_id='1')
        sync_platform_collections()
        job = FetchJob.objects.create(kind='refresh')

        def stream(*args, **kwargs):
            self.game.refresh_from_db()
            self.assertEqual(self.game.year, 2000)
            yield ['1'], {'1': game_detail(year=2020)}, {'1': {}}

        with patch('games.tasks.BGGClient') as client:
            client.return_value.fetch_top_games_ranks.return_value = {'1': game_info(1)}
            client.return_value.stream_details_batches.side_effect = stream
            run_refresh.now(job.pk, 1, [], 20, 'https://example.invalid/ranks.zip')
        job.refresh_from_db()
        self.game.refresh_from_db()
        self.assertEqual(job.status, 'done')
        self.assertEqual(self.game.year, 2020)
        self.assertEqual(self.game.owned_by, ['Board Game Arena'])

    def test_incomplete_top_n_preserves_polls_and_year(self):
        job = FetchJob.objects.create(kind='top_n')
        with patch('games.tasks.BGGClient') as client:
            client.return_value.fetch_top_games_ranks.return_value = {'1': game_info(1)}
            client.return_value.fetch_details_batches.return_value = ({}, {})
            run_fetch_top_n.now(job.pk, 1, 'https://example.invalid/ranks.zip')
        job.refresh_from_db()
        self.game.refresh_from_db()
        self.assertEqual(job.status, 'error')
        self.assertEqual(self.game.year, 2000)
        self.assertEqual(self.game.player_counts.count(), 1)

    def test_completion_record_failure_rolls_back_catalog(self):
        job = FetchJob.objects.create(kind='top_n')
        original_save = FetchJob.save

        def fail_done(instance, *args, **kwargs):
            if instance.status == 'done':
                raise RuntimeError('completion write failed')
            return original_save(instance, *args, **kwargs)

        with patch('games.tasks.BGGClient') as client, patch.object(FetchJob, 'save', fail_done):
            client.return_value.fetch_top_games_ranks.return_value = {'1': game_info(1, 'Changed')}
            client.return_value.fetch_details_batches.return_value = ({'1': game_detail(year=2020)}, {'1': {}})
            run_fetch_top_n.now(job.pk, 1, 'https://example.invalid/ranks.zip')
        self.game.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(job.status, 'error')
        self.assertEqual(self.game.title, 'Original')
        self.assertEqual(self.game.year, 2000)
        self.assertEqual(self.game.player_counts.count(), 1)

    def test_refresh_later_batch_failure_leaves_live_data_untouched(self):
        job = FetchJob.objects.create(kind='refresh')

        def stream(*args, **kwargs):
            yield ['1'], {'1': game_detail(year=2020)}, {'1': {}}
            raise RuntimeError('later request failed')

        with patch('games.tasks.BGGClient') as client:
            client.return_value.fetch_top_games_ranks.return_value = {'1': game_info(1)}
            client.return_value.stream_details_batches.side_effect = stream
            run_refresh.now(job.pk, 1, [], 20, 'https://example.invalid/ranks.zip')
        self.game.refresh_from_db()
        self.assertEqual(self.game.year, 2000)
        self.assertEqual(self.game.player_counts.count(), 1)
        self.assertTrue(Collection.objects.filter(pk=self.collection.pk).exists())

    def test_empty_collection_clears_flags_outside_payload(self):
        _sync_catalog({}, {}, {}, collection_owned_map={'alice': set()})
        self.game.refresh_from_db()
        self.assertFalse(self.game.owned)
        self.assertEqual(self.game.owned_by, [])
        self.assertFalse(OwnedGame.objects.filter(collection=self.collection).exists())


class SourceValidationTests(SimpleTestCase):
    def test_truncated_collection_is_rejected(self):
        client = BGGClient(throttle_sec=0)
        with patch.object(client.session, 'get', return_value=FakeResp(b'<items totalitems="2"><item objectid="1"/></items>')):
            with self.assertRaisesRegex(RuntimeError, 'Incomplete BGG collection'):
                client.fetch_owned_collection('alice')

    def test_collection_error_is_not_empty_collection(self):
        client = BGGClient(throttle_sec=0)
        with patch.object(client.session, 'get', return_value=FakeResp(b'<errors><error/></errors>')):
            with self.assertRaises(RuntimeError):
                client.fetch_owned_collection('invalid')

    def test_missing_detail_ids_are_rejected(self):
        client = BGGClient(throttle_sec=0)
        with patch.object(client, '_get', return_value=FakeResp(b'<items/>')):
            with self.assertRaises(RuntimeError):
                client.fetch_details_batches(['1'])

    def test_rtt_failed_page_does_not_produce_prunable_subset(self):
        client = RTTClient(throttle_sec=0)
        with patch.object(client, 'fetch_library_slugs', return_value=[('a', 'A'), ('b', 'B')]), \
                patch.object(client, 'fetch_game_bgg_id', side_effect=['1', requests.ConnectionError()]):
            with self.assertRaises(RuntimeError):
                client.fetch_games()

