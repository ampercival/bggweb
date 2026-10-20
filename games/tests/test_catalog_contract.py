import csv
import io
from urllib.parse import parse_qs

from django.http import QueryDict
from django.test import TestCase, SimpleTestCase, override_settings

from games.catalog import COLUMNS
from games.filters import normalize_filters
from games.forms import ImportForm
from games.models import Family, Game, PlayerCountRecommendation
from games.utils import GameFilter
from .test_views import VIEW_TEST_SETTINGS


class FilterValidationTests(SimpleTestCase):
    def test_nonfinite_invalid_and_reversed_ranges(self):
        values = normalize_filters(QueryDict('min_avg_rating=nan&max_weight=garbage&min_year=2020&max_year=2000&page_size=99999'))
        self.assertEqual(values['min_avg_rating'], '0.0')
        self.assertEqual(values['max_weight'], '5.0')
        self.assertEqual(values['min_year'], '2000')
        self.assertEqual(values['max_year'], '2020')
        self.assertEqual(values['page_size'], '1000')

    def test_multiple_mechanics_round_trip(self):
        values = normalize_filters(QueryDict('mechanics=Trading&mechanics=Dice+Rolling'))
        self.assertEqual(parse_qs(values.urlencode())['mechanics'], ['Dice Rolling', 'Trading'])

    def test_import_input_validation(self):
        self.assertFalse(ImportForm({'action': 'top_n', 'n': '-1', 'zip_url': 'http://localhost/data.zip'}).is_valid())
        self.assertFalse(ImportForm({'action': 'collection', 'username': ''}).is_valid())
        form = ImportForm({'action': 'refresh', 'n': '100', 'zip_url': 'https://boardgamegeek.com/data_dumps/bg_ranks/'})
        self.assertFalse(form.is_valid())
        self.assertIn('This is the BGG download page', str(form.errors))


@override_settings(**VIEW_TEST_SETTINGS)
class CatalogContractTests(TestCase):
    def setUp(self):
        for gid, best, rating in [('1', 100, 1), ('2', 0, 10)]:
            game = Game.objects.create(bgg_id=gid, title='=formula' if gid == '1' else 'Other',
                                       type='Base Game', avg_rating=rating)
            game.families.add(Family.objects.get_or_create(name='Strategy')[0])
            PlayerCountRecommendation.objects.create(game=game, count=4, best_pct=best,
                                                       notrec_pct=100-best, vote_count=10)

    def test_malformed_filters_render_without_server_error(self):
        self.assertEqual(self.client.get('/games/?min_avg_rating=abc&max_weight=inf').status_code, 200)

    def test_playable_sort_uses_recommendation_not_rating(self):
        qs, *_ = GameFilter(QueryDict('playable=all&sort=playable&dir=desc')).get_queryset()
        self.assertEqual(list(qs.values_list('game__bgg_id', flat=True)), ['1', '2'])

    def test_export_semantics_and_formula_protection(self):
        response = self.client.get('/export.csv?playable=all&q=formula')
        rows = list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode())))
        self.assertEqual(rows[0], [label for _, label, _ in COLUMNS])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "'=formula")
        self.assertEqual(rows[1][11], 'Strategy')

    def test_protected_route_has_real_login_target(self):
        response = self.client.get('/refresh/')
        self.assertTrue(response['Location'].startswith('/admin/login/'))
        self.assertEqual(self.client.get(response['Location']).status_code, 200)

    def test_ajax_returns_canonical_filters_and_facet_html(self):
        response = self.client.get('/games/rows/?playable=all&mechanics=Trading&mechanics=Dice+Rolling')
        data = response.json()
        self.assertEqual(parse_qs(data['query'])['mechanics'], ['Dice Rolling', 'Trading'])
        self.assertIn('name="mechanics"', data['facets_html'])
