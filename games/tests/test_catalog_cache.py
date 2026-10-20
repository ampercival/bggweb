from django.core.cache import cache
from django.http import QueryDict
from django.test import TestCase, override_settings

from games.catalog_cache import invalidate_catalog
from games.models import Game, PlayerCountRecommendation
from games.utils import GameFilter


@override_settings(CATALOG_CACHE_ENABLED=True)
class CatalogCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        game = Game.objects.create(bgg_id='1', title='G', type='Base Game', avg_rating=8)
        self.rec = PlayerCountRecommendation.objects.create(game=game, count=4, best_pct=100, vote_count=30)
        invalidate_catalog()

    def test_cached_normalization_and_facets_skip_aggregates(self):
        first = GameFilter(QueryDict())
        _, pre, *_ = first.get_queryset()
        first.get_category_counts(pre)
        with self.assertNumQueries(1):  # Shared revision lookup only.
            second = GameFilter(QueryDict())
            _, pre, *_ = second.get_queryset()
            second.get_category_counts(pre)

    def test_revision_changes_normalization_after_successful_write(self):
        _, _, _, low = GameFilter(QueryDict()).get_queryset()
        self.assertEqual(low, 300)
        self.rec.best_pct = 50
        self.rec.save()
        invalidate_catalog()
        _, _, _, low = GameFilter(QueryDict()).get_queryset()
        self.assertEqual(low, 150)

    def test_filter_specific_cache_does_not_change_global_scale(self):
        second = Game.objects.create(bgg_id='2', title='Other', type='Base Game')
        PlayerCountRecommendation.objects.create(game=second, count=2, notrec_pct=100, vote_count=30)
        invalidate_catalog()
        _, _, width, low = GameFilter(QueryDict()).get_queryset()
        _, _, filtered_width, filtered_low = GameFilter(QueryDict('q=G')).get_queryset()
        self.assertEqual((width, low), (filtered_width, filtered_low))
