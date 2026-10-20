import csv
import io
from unittest import mock

from django.http import QueryDict
from django.test import SimpleTestCase, TestCase, override_settings

from games.filters import normalize_filters
from games.models import Game, PlayerCountRecommendation
from games.playtime import playtime_fields
from games.services.bgg_client import BGGClient
from games.tasks import _sync_catalog, _sync_refresh_chunk
from games.utils import GameFilter
from .factories import game_detail, game_info
from .test_bgg_client import FakeResp
from .test_views import VIEW_TEST_SETTINGS


class PlaytimeParsingTests(SimpleTestCase):
    def test_parses_three_bgg_time_fields_and_unknown_values(self):
        xml = b'''<items>
          <item id="1"><playingtime value="90"/><minplaytime value="45"/><maxplaytime value="90"/></item>
          <item id="2"><playingtime value="0"/><minplaytime value="bad"/><maxplaytime value="-5"/></item>
          <item id="3"/>
        </items>'''
        client = BGGClient(throttle_sec=0, detail_throttle_sec=0)
        with mock.patch.object(client, '_get', return_value=FakeResp(xml)):
            details, _ = client.fetch_details_batches(['1', '2', '3'])
        self.assertEqual([details['1'][key] for key in ['Playing Time', 'Min Playtime', 'Max Playtime']], [90, 45, 90])
        for gid in ['2', '3']:
            self.assertEqual([details[gid][key] for key in ['Playing Time', 'Min Playtime', 'Max Playtime']], [None, None, None])

    def test_invalid_filters_are_ignored_and_reversed_bounds_normalized(self):
        for value in ['nan', 'inf', '-1', '0', 'bad']:
            filters = normalize_filters(QueryDict('min_playtime=' + value + '&max_playtime=' + value))
            self.assertEqual((filters['min_playtime'], filters['max_playtime']), ('', ''))
        filters = normalize_filters(QueryDict('min_playtime=120&max_playtime=30'))
        self.assertEqual((filters['min_playtime'], filters['max_playtime']), ('30', '120'))

    def test_inconsistent_single_time_does_not_create_reversed_fallback_range(self):
        fields = playtime_fields({'Playing Time': 20, 'Min Playtime': 30})
        self.assertEqual(fields, {'playing_time': None, 'min_playtime': 30, 'max_playtime': None})


class PlaytimeSyncTests(TestCase):
    def test_both_sync_paths_create_update_and_clear_times(self):
        def sync_chunk(info, details, counts):
            return _sync_refresh_chunk(list(info), info, details, counts, {}, {})

        for sync in [_sync_catalog, sync_chunk]:
            with self.subTest(sync=sync.__name__):
                Game.objects.all().delete()
                info = {'1': game_info(1)}
                sync(info, {'1': game_detail(playing_time=90, min_playtime=45, max_playtime=90)}, {'1': {}})
                game = Game.objects.get(bgg_id='1')
                self.assertEqual((game.playing_time, game.min_playtime, game.max_playtime), (90, 45, 90))
                sync(info, {'1': game_detail(playing_time=60, min_playtime=30, max_playtime=60)}, {'1': {}})
                game.refresh_from_db()
                self.assertEqual((game.playing_time, game.min_playtime, game.max_playtime), (60, 30, 60))
                sync(info, {'1': game_detail(playing_time=0, min_playtime=-1, max_playtime='bad')}, {'1': {}})
                game.refresh_from_db()
                self.assertEqual((game.playing_time, game.min_playtime, game.max_playtime), (None, None, None))

    def test_inverted_source_range_is_unknown_with_single_duration_fallback(self):
        _sync_catalog({'1': game_info(1)},
                      {'1': game_detail(playing_time=90, min_playtime=120, max_playtime=60)}, {'1': {}})
        game = Game.objects.get(bgg_id='1')
        self.assertEqual((game.min_playtime, game.max_playtime, game.playtime_label), (None, None, '90'))


@override_settings(**VIEW_TEST_SETTINGS)
class PlaytimeCatalogTests(TestCase):
    def setUp(self):
        for gid, low, high, single in [('1', 30, 60, 60), ('2', 45, 90, 90),
                                       ('3', None, None, 45), ('4', None, None, None)]:
            game = Game.objects.create(bgg_id=gid, title='Game ' + gid, type='Base Game',
                                       min_playtime=low, max_playtime=high, playing_time=single)
            PlayerCountRecommendation.objects.create(game=game, count=4, best_pct=100, vote_count=50)

    def ids(self, query):
        qs, *_ = GameFilter(QueryDict(query)).get_queryset()
        return list(qs.values_list('game__bgg_id', flat=True))

    def test_maximum_uses_entire_range_and_single_duration_fallback(self):
        self.assertEqual(set(self.ids('max_playtime=60')), {'1', '3'})
        self.assertEqual(set(self.ids('min_playtime=45&max_playtime=90')), {'2', '3'})
        self.assertEqual(self.ids('min_playtime=60&max_playtime=60'), [])

    def test_unknown_times_included_only_without_active_time_bounds(self):
        self.assertEqual(set(self.ids('')), {'1', '2', '3', '4'})
        self.assertNotIn('4', self.ids('min_playtime=1'))
        self.assertNotIn('4', self.ids('max_playtime=9999'))

    def test_time_sort_uses_maximum_and_keeps_unknown_last(self):
        self.assertEqual(self.ids('sort=playtime&dir=asc'), ['3', '1', '2', '4'])
        self.assertEqual(self.ids('sort=playtime&dir=desc'), ['2', '1', '3', '4'])

    def test_page_ajax_export_and_detail_agree(self):
        query = '?min_playtime=30&max_playtime=60'
        page = self.client.get('/games/' + query)
        self.assertContains(page, 'name="max_playtime"')
        self.assertContains(page, 'value="60"')
        self.assertContains(page, '30\u201360')
        self.assertNotContains(page, 'Game 2')
        data = self.client.get('/games/rows/' + query).json()
        self.assertEqual(data['count'], 2)
        self.assertIn('max_playtime=60', data['query'])
        self.assertNotIn('Game 2', data['html'])
        response = self.client.get('/export.csv' + query)
        rows = list(csv.DictReader(io.StringIO(b''.join(response.streaming_content).decode())))
        self.assertEqual({row['Game'] for row in rows}, {'Game 1', 'Game 3'})
        row = next(row for row in rows if row['Game'] == 'Game 1')
        self.assertEqual(row['Play time (min)'], '30\u201360')
        self.assertEqual(row['BGG minimum time (min)'], '30')
        self.assertEqual(row['BGG maximum time (min)'], '60')
        self.assertContains(self.client.get('/games/1/'), '30\u201360 minutes')
        self.assertContains(self.client.get('/games/4/'), 'Unknown')
