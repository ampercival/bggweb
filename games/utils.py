from datetime import datetime
from django.db.models import (
    Case,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    Max,
    Min,
    OrderBy,
    Q,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce
from .models import BGGUser, Collection, PlayerCountRecommendation

class GameFilter:
    def __init__(self, request_get):
        self.params = request_get
        self.year_slider_min = 1900
        self.year_slider_max = datetime.now().year

    def get_queryset(self):
        # Initial QuerySet
        qs = (
            PlayerCountRecommendation.objects.select_related('game')
            .prefetch_related('game__categories', 'game__families')
        )
        
        # Annotations (moved from views.py)
        qs = qs.annotate(
            best_pct_c=Coalesce('best_pct', Value(0.0)),
            rec_pct_c=Coalesce('rec_pct', Value(0.0)),
            not_pct_c=Coalesce('notrec_pct', Value(0.0)),
            avg_rating_co=Coalesce('game__avg_rating', Value(0.0)),
            weight_co=Coalesce('game__weight', Value(0.0)),
            num_voters_co=Coalesce('game__num_voters', Value(0)),
            year_int=Case(
                When(game__year__regex=r'^\d+$', then=Cast('game__year', IntegerField())),
                default=None,
                output_field=IntegerField(),
            ),
        ).annotate(
            pc_score_unadj=ExpressionWrapper(
                F('best_pct_c') * Value(3.0)
                + F('rec_pct_c') * Value(2.0)
                - F('not_pct_c') * Value(2.0),
                output_field=FloatField(),
            )
        )
        
        qs_for_norm = qs  # Keep reference for aggregation later

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
            qs = qs.filter(pc_score_unadj__gte=150)
        elif playable_state == 'not':
            qs = qs.filter(pc_score_unadj__lt=150)

        player_count = self.params.get('player_count', 'all')
        if player_count.isdigit():
            qs = qs.filter(count=int(player_count))
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

        categories = self._get_list('categories')
        qs_pre_category = qs # Snapshot before cat filter for counts
        if categories:
            qs = qs.filter(
                Q(game__categories__name__in=categories) |
                Q(game__families__name__in=categories)
            ).distinct()

        # PC Score Normalization
        pc_stats = qs_for_norm.aggregate(min_pc=Min('pc_score_unadj'), max_pc=Max('pc_score_unadj'))
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
                ((F('avg_rating_co') * Value(3.0)) + (F('pc_score') * Value(1.0))) / Value(4.0),
                output_field=FloatField(),
            )
        )

        # Sorting
        sort = self.params.get('sort', 'score_factor')
        direction = self.params.get('dir', 'desc')
        order_map = {
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
        qs = qs.order_by(ordering, secondary_order)

        return qs, qs_pre_category, pc_range, pc_min

    def get_category_counts(self, qs):
        # Counts based on qs_pre_category to show available options
        cat_counts = {}
        fam_counts = {}
        
        for row in qs.values('game__categories__name').exclude(game__categories__name__isnull=True).annotate(count=Count('id')):
           name = row['game__categories__name']
           if name: cat_counts[name] = row['count']
           
        for row in qs.values('game__families__name').exclude(game__families__name__isnull=True).annotate(count=Count('id')):
           name = row['game__families__name']
           if name: fam_counts[name] = row['count']
           
        return cat_counts, fam_counts

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


def serialize_game_row(rec, pc_range, pc_min):
    game = rec.game
    categories = list(game.categories.values_list('name', flat=True))
    families = list(game.families.values_list('name', flat=True))
    owners_list = sorted(list(game.owned_by or []))
    pc_unadj = float(rec.pc_score_unadj or 0.0)
    
    if pc_range:
        pc_score_val = (pc_unadj - pc_min) / pc_range * 10.0
    else:
        pc_score_val = 0.0
        
    score_factor_val = float(rec.score_factor or 0.0)
    
    return {
        'title': game.title,
        'game_id': game.bgg_id,
        'year': game.year,
        'bgg_rank': game.bgg_rank,
        'avg_rating': game.avg_rating,
        'num_voters': game.num_voters,
        'weight': game.weight,
        'weight_votes': game.weight_votes,
        'owned': game.owned,
        'owned_by': owners_list,
        'owned_by_str': ', '.join(owners_list),
        'type': game.type,
        'categories': categories,
        'categories_str': ', '.join(sorted(categories)),
        'families': families,
        'families_str': ', '.join(sorted(families)),
        'player_count': rec.count,
        'best_pct': rec.best_pct or 0.0,
        'best_votes': rec.best_votes or 0,
        'rec_pct': rec.rec_pct or 0.0,
        'rec_votes': rec.rec_votes or 0,
        'not_pct': rec.notrec_pct or 0.0,
        'not_votes': rec.notrec_votes or 0,
        'total_votes': rec.vote_count or 0,
        'pc_score_unadj': round(pc_unadj, 1),
        'pc_score': round(pc_score_val, 2),
        'score_factor': round(score_factor_val, 3),
        'playable': 'Playable' if pc_unadj >= 150 else 'Not Playable',
    }
