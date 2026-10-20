from datetime import datetime
from django.db.models import Case, When, IntegerField, Count, ExpressionWrapper, F, FloatField, Max, Min, OrderBy, Q, Value
from django.db.models.functions import Coalesce
from .models import PlayerCountRecommendation
from .filters import normalize_filters
from .catalog_cache import cached_catalog, catalog_version, facet_key
from .scoring import PLAYABLE_THRESHOLD, RATING_WEIGHT, raw_player_score


class GameFilter:
    def __init__(self, request_get):
        self.params = normalize_filters(request_get)
        self.revision = catalog_version()
        self.year_slider_min = 1900
        self.year_slider_max = datetime.now().year

    def get_queryset(self):
        # Initial QuerySet
        qs = (
            PlayerCountRecommendation.objects.select_related('game')
            .prefetch_related('game__categories', 'game__families', 'game__mechanics')
        )
        
        # Annotations (moved from views.py)
        qs = qs.annotate(
            best_pct_c=Coalesce('best_pct', Value(0.0)),
            rec_pct_c=Coalesce('rec_pct', Value(0.0)),
            not_pct_c=Coalesce('notrec_pct', Value(0.0)),
            avg_rating_co=Coalesce('game__avg_rating', Value(0.0)),
            weight_co=Coalesce('game__weight', Value(0.0)),
            num_voters_co=Coalesce('game__num_voters', Value(0)),
            year_int=F('game__year'),
        ).annotate(
            pc_score_unadj=raw_player_score()
        )

        # Normalization baseline: the min/max used to scale pc_score is taken
        # across ALL player-count rows (before user filters are applied), so the
        # 0-10 scale stays stable no matter how the results are filtered.
        qs_for_norm = qs

        # Filtering
        q = self.params.get('q', '')
        if q:
            qs = qs.filter(game__title__icontains=q)

        owners = self._get_list('owners')
        if owners:
            qs = qs.filter(game__ownedgame__collection__username__in=owners).distinct()

        type_filter = self.params.get('type', 'base')
        if type_filter == 'base':
            qs = qs.filter(game__type='Base Game')
        elif type_filter == 'expansion':
            qs = qs.filter(game__type='Expansion')

        playable_state = self.params.get('playable', 'playable')
        if playable_state == 'playable':
            qs = qs.filter(pc_score_unadj__gte=PLAYABLE_THRESHOLD)
        elif playable_state == 'not':
            qs = qs.filter(pc_score_unadj__lt=PLAYABLE_THRESHOLD, vote_count__gt=0)

        player_count = self.params.get('player_count', 'all')
        if player_count.isdigit():
            qs = qs.filter(count=int(player_count), is_open_ended=False)
        elif player_count == '8plus':
            qs = qs.filter(count__gte=8)

        min_year = self._to_int(self.params.get('min_year')) or self.year_slider_min
        max_year = self._to_int(self.params.get('max_year')) or self.year_slider_max
        strict_year = (min_year > self.year_slider_min) or (max_year < self.year_slider_max)
        qs = qs.filter(Q(year_int__gte=min_year) | Q(year_int__isnull=True))
        qs = qs.filter(Q(year_int__lte=max_year) | Q(year_int__isnull=True))
        if strict_year:
            qs = qs.filter(year_int__isnull=False)

        min_avg = self._to_float(self.params.get('min_avg_rating')) if self.params.get('min_avg_rating') else 0.0
        max_avg = self._to_float(self.params.get('max_avg_rating')) if self.params.get('max_avg_rating') else 10.0
        qs = qs.filter(avg_rating_co__gte=min_avg, avg_rating_co__lte=max_avg)

        min_weight = self._to_float(self.params.get('min_weight')) if self.params.get('min_weight') else 0.0
        max_weight = self._to_float(self.params.get('max_weight')) if self.params.get('max_weight') else 5.0
        qs = qs.filter(weight_co__gte=min_weight, weight_co__lte=max_weight)

        min_voters = self._to_int(self.params.get('min_voters'))
        if min_voters is not None:
            qs = qs.filter(num_voters_co__gte=min_voters)

        qs = qs.filter(vote_count__gte=int(self.params['min_poll_votes']))

        categories = self._get_list('categories')
        mechanics = self._get_list('mechanics')
        qs_pre_category = qs # Snapshot before cat filter for counts
        if categories:
            qs = qs.filter(
                Q(game__categories__name__in=categories) |
                Q(game__families__name__in=categories)
            ).distinct()
            
        if mechanics:
            qs = qs.filter(game__mechanics__name__in=mechanics).distinct()

        # PC Score Normalization
        pc_stats = cached_catalog(f'catalog:normalization:{self.revision}', lambda: qs_for_norm.aggregate(min_pc=Min('pc_score_unadj'), max_pc=Max('pc_score_unadj')))
        pc_min = pc_stats['min_pc']
        pc_max = pc_stats['max_pc']
        pc_range = None
        
        if pc_min is None or pc_max is None or pc_max <= pc_min:
            qs = qs.annotate(pc_score=Value(0.0, output_field=FloatField()))
        else:
            pc_range = pc_max - pc_min
            qs = qs.annotate(
                pc_score=ExpressionWrapper(
                    (F('pc_score_unadj') - Value(pc_min)) / Value(pc_range) * Value(10.0),
                    output_field=FloatField(),
                )
            )

        # Score Factor
        qs = qs.annotate(
            score_factor=ExpressionWrapper(
                ((F('avg_rating_co') * Value(RATING_WEIGHT)) + (F('pc_score') * Value(1.0))) / Value(4.0),
                output_field=FloatField(),
            )
        )

        qs = qs.annotate(playable_order=Case(When(pc_score_unadj__gte=PLAYABLE_THRESHOLD, then=Value(1)),
                                             default=Value(0), output_field=IntegerField()))
        # Sorting
        sort = self.params.get('sort', 'score_factor')
        direction = self.params.get('dir', 'desc')
        order_map = {
            'playable': F('playable_order'),
            'title': F('game__title'),
            'game_id': F('game__bgg_id'),
            'year': F('year_int'),
            'bgg_rank': F('game__bgg_rank'),
            'avg_rating': F('avg_rating_co'),
            'num_voters': F('num_voters_co'),
            'weight': F('weight_co'),
            'weight_votes': F('game__weight_votes'),
            'owned': F('game__owned'),
            'type': F('game__type'),
            'player_count': F('count'),
            'best_pct': F('best_pct_c'),
            'best_votes': F('best_votes'),
            'rec_pct': F('rec_pct_c'),
            'rec_votes': F('rec_votes'),
            'not_pct': F('not_pct_c'),
            'not_votes': F('notrec_votes'),
            'total_votes': F('vote_count'),
            'pc_score_unadj': F('pc_score_unadj'),
            'pc_score': F('pc_score'),
            'score_factor': F('score_factor'),
        }
        order_expr = order_map.get(sort, F('score_factor'))
        ordering = OrderBy(order_expr, descending=(direction == 'desc'), nulls_last=True)
        secondary_order = OrderBy(F('game__title'), nulls_last=True)
        qs = qs.order_by(ordering, secondary_order, 'game__bgg_id', 'count', 'is_open_ended')

        return qs, qs_pre_category, pc_range, pc_min

    def get_category_counts(self, qs):
        return cached_catalog(facet_key(self.revision, self.params), lambda: self._category_counts(qs))

    def _category_counts(self, qs):
        # Counts based on qs_pre_category to show available options
        cat_counts = {}
        fam_counts = {}
        mec_counts = {}
        
        for row in qs.values('game__categories__name').exclude(game__categories__name__isnull=True).annotate(count=Count('id', distinct=True)):
            name = row['game__categories__name']
            if name:
                cat_counts[name] = row['count']

        for row in qs.values('game__families__name').exclude(game__families__name__isnull=True).annotate(count=Count('id', distinct=True)):
            name = row['game__families__name']
            if name:
                fam_counts[name] = row['count']

        for row in qs.values('game__mechanics__name').exclude(game__mechanics__name__isnull=True).annotate(count=Count('id', distinct=True)):
            name = row['game__mechanics__name']
            if name:
                mec_counts[name] = row['count']

        return cat_counts, fam_counts, mec_counts

    def _get_list(self, key):
        raw = self.params.getlist(key)
        return [x.strip() for x in raw if x.strip()]

    def _to_int(self, val):
        try:
            return int(val) if val not in (None, '') else None
        except (TypeError, ValueError):
            return None

    def _to_float(self, val):
         try:
            return float(val) if val not in (None, '') else None
         except (TypeError, ValueError):
            return None


