from django.http import QueryDict
from django.test import TestCase, override_settings

from games.models import Game, PlayerCountRecommendation
from games.tasks import _sync_catalog
from games.utils import GameFilter, serialize_game_row
from .factories import game_info, game_detail, player_count
from .test_views import VIEW_TEST_SETTINGS


@override_settings(**VIEW_TEST_SETTINGS)
class EvidenceTests(TestCase):
    def test_exact_and_open_ended_buckets_coexist_and_export(self):
        _sync_catalog({'1': game_info(1)}, {'1': game_detail()},
                      {'1': {'8': player_count(best_votes=100), '8+': player_count(best_votes=3)}})
        game = Game.objects.get(bgg_id='1')
        self.assertEqual(game.player_counts.count(), 2)
        qs, *_ = GameFilter(QueryDict('player_count=8')).get_queryset()
        self.assertEqual(qs.count(), 1)
        self.assertFalse(qs.first().is_open_ended)
        qs, *_ = GameFilter(QueryDict('player_count=8plus')).get_queryset()
        self.assertEqual(qs.count(), 2)
        response = self.client.get('/export.csv?player_count=8plus')
        self.assertIn(b'8+', b''.join(response.streaming_content))

    def test_poll_vote_minimum_uses_poll_sample(self):
        _sync_catalog({'1': game_info(1)}, {'1': game_detail()},
                      {'1': {'2': player_count(best_votes=3), '4': player_count(best_votes=100)}})
        qs, *_ = GameFilter(QueryDict('min_poll_votes=20')).get_queryset()
        self.assertEqual(list(qs.values_list('count', flat=True)), [4])

    def test_games_with_no_polls_are_discoverable(self):
        Game.objects.create(bgg_id='1', title='Missing poll game', type='Base Game')
        self.assertContains(self.client.get('/games/no-polls/'), 'Missing poll game')

    def test_zero_votes_are_not_a_negative_recommendation(self):
        game = Game.objects.create(bgg_id='1', title='No votes', type='Base Game')
        PlayerCountRecommendation.objects.create(game=game, count=4)
        qs, _, width, low = GameFilter(QueryDict('playable=all')).get_queryset()
        self.assertEqual(serialize_game_row(qs.first(), width, low)['playable'], 'No poll votes')
        qs, *_ = GameFilter(QueryDict('playable=not')).get_queryset()
        self.assertFalse(qs.exists())
