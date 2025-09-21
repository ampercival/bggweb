from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.core.paginator import Paginator
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
from urllib.parse import urlencode
from django.template.loader import render_to_string
from datetime import datetime
from .models import Game, PlayerCountRecommendation, FetchJob
from .tasks import start_background, run_fetch_top_n, run_fetch_collection, run_refresh
import csv


def home(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'top_n':
            try:
                n = int(request.POST.get('n') or '100')
            except ValueError:
                n = 100
            zip_url = (request.POST.get('zip_url') or '').strip()
            params = {'n': n}
            if zip_url:
                params['zip_url'] = zip_url
            job = FetchJob.objects.create(kind='top_n', params=params, status='pending', total=n)
            start_background(run_fetch_top_n, job.id, n, zip_url or None)
            return redirect('job_detail', job_id=job.id)
        elif action == 'collection':
            username = request.POST.get('username') or ''
            job = FetchJob.objects.create(kind='collection', params={'username': username}, status='pending')
            start_background(run_fetch_collection, job.id, username)
            return redirect('job_detail', job_id=job.id)
        elif action == 'refresh':
            try:
                n = int(request.POST.get('n') or '100')
            except ValueError:
                n = 100
            username = request.POST.get('username') or ''
            zip_url = (request.POST.get('zip_url') or '').strip()
            params = {'n': n, 'username': username, 'batch_size': 20}
            if zip_url:
                params['zip_url'] = zip_url
            job = FetchJob.objects.create(kind='refresh', params=params, status='pending', total=n)
            start_background(run_refresh, job.id, n, username, 20, zip_url or None)
            return redirect('job_detail', job_id=job.id)
    latest_jobs = FetchJob.objects.order_by('-created_at')[:10]
    last_refresh = FetchJob.objects.filter(kind='refresh').order_by('-finished_at', '-created_at').first()
    return render(request, 'home.html', {
        'jobs': latest_jobs,
        'last_refresh': last_refresh,
    })


def job_detail(request, job_id: int):
    job = get_object_or_404(FetchJob, id=job_id)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'status': job.status,
            'progress': job.progress,
            'total': job.total,
            'error': job.error,
            'phases': (job.params or {}).get('phases', {}),
        })
    return render(request, 'job_detail.html', {'job': job})


def clear_jobs(request):
    if request.method != 'POST':
        return HttpResponse(status=405)
    FetchJob.objects.all().delete()
    return redirect('home')


def _compute_rows_context(request):
    sort = request.GET.get('sort', 'score_factor')
    direction = request.GET.get('dir', 'desc')

    q = request.GET.get('q', '')
    owned_state = request.GET.get('owned_state', 'all')
    type_filter = request.GET.get('type', 'base')
    playable_state = request.GET.get('playable', 'playable')
    player_count_filter = request.GET.get('player_count', 'all')
    min_year_param = request.GET.get('min_year')
    max_year_param = request.GET.get('max_year')
    min_avg_rating_param = request.GET.get('min_avg_rating')
    max_avg_rating_param = request.GET.get('max_avg_rating')
    min_weight_param = request.GET.get('min_weight')
    max_weight_param = request.GET.get('max_weight')
    min_voters_param = request.GET.get('min_voters')
    selected_categories = request.GET.getlist('categories')

    def to_int(val):
        try:
            return int(val) if val not in (None, '') else None
        except (TypeError, ValueError):
            return None

    def to_float(val):
        try:
            return float(val) if val not in (None, '') else None
        except (TypeError, ValueError):
            return None

    year_slider_min = 1900
    year_slider_max = datetime.now().year
    min_year = to_int(min_year_param) or year_slider_min
    max_year = to_int(max_year_param) or year_slider_max
    min_avg_rating = to_float(min_avg_rating_param)
    if min_avg_rating is None:
        min_avg_rating = 0.0
    max_avg_rating = to_float(max_avg_rating_param)
    if max_avg_rating is None:
        max_avg_rating = 10.0
    min_weight = to_float(min_weight_param)
    if min_weight is None:
        min_weight = 0.0
    max_weight = to_float(max_weight_param)
    if max_weight is None:
        max_weight = 5.0
    min_voters = to_int(min_voters_param)

    qs = (
        PlayerCountRecommendation.objects.select_related('game')
        .prefetch_related('game__categories', 'game__families')
    )
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

    qs_for_norm = qs

    if q:
        qs = qs.filter(game__title__icontains=q)

    if owned_state == 'owned':
        qs = qs.filter(game__owned=True)
    elif owned_state == 'not':
        qs = qs.filter(game__owned=False)

    if type_filter == 'base':
        qs = qs.filter(game__type='Base Game')
    elif type_filter == 'expansion':
        qs = qs.filter(game__type='Expansion')

    if playable_state == 'playable':
        qs = qs.filter(pc_score_unadj__gte=150)
    elif playable_state == 'not':
        qs = qs.filter(pc_score_unadj__lt=150)

    if player_count_filter.isdigit():
        qs = qs.filter(count=int(player_count_filter))
    elif player_count_filter == '8plus':
        qs = qs.filter(count__gte=8)

    strict_year = (min_year > year_slider_min) or (max_year < year_slider_max)
    qs = qs.filter(Q(year_int__gte=min_year) | Q(year_int__isnull=True))
    qs = qs.filter(Q(year_int__lte=max_year) | Q(year_int__isnull=True))
    if strict_year:
        qs = qs.filter(year_int__isnull=False)

    qs = qs.filter(avg_rating_co__gte=min_avg_rating, avg_rating_co__lte=max_avg_rating)
    qs = qs.filter(weight_co__gte=min_weight, weight_co__lte=max_weight)
    if min_voters is not None:
        qs = qs.filter(num_voters_co__gte=min_voters)

    qs_pre_category = qs

    category_counts = {}
    for row in qs_pre_category.values('game__categories__name').exclude(game__categories__name__isnull=True).annotate(count=Count('id')):
        name = row['game__categories__name']
        if name:
            category_counts[name] = row['count']

    family_counts = {}
    for row in qs_pre_category.values('game__families__name').exclude(game__families__name__isnull=True).annotate(count=Count('id')):
        name = row['game__families__name']
        if name:
            family_counts[name] = row['count']

    if selected_categories:
        qs = qs.filter(
            Q(game__categories__name__in=selected_categories) |
            Q(game__families__name__in=selected_categories)
        ).distinct()

    pc_stats = qs_for_norm.aggregate(min_pc=Min('pc_score_unadj'), max_pc=Max('pc_score_unadj'))
    pc_min = pc_stats['min_pc']
    pc_max = pc_stats['max_pc']

    if pc_min is None or pc_max is None or pc_max <= pc_min:
        qs = qs.annotate(pc_score=Value(0.0, output_field=FloatField()))
        pc_range = None
    else:
        pc_range = pc_max - pc_min
        qs = qs.annotate(
            pc_score=ExpressionWrapper(
                (F('pc_score_unadj') - Value(pc_min)) / Value(pc_range) * Value(10.0),
                output_field=FloatField(),
            )
        )

    qs = qs.annotate(
        score_factor=ExpressionWrapper(
            ((F('avg_rating_co') * Value(3.0)) + (F('pc_score') * Value(1.0))) / Value(4.0),
            output_field=FloatField(),
        )
    )

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

    def to_page_int(val, default):
        try:
            v = int(val)
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default

    page_number = to_page_int(request.GET.get('page'), 1)
    page_size = to_page_int(request.GET.get('page_size'), 50)
    if page_size > 1000:
        page_size = 1000

    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(page_number)
    rows_count = paginator.count
    rows = []

    for rec in page_obj.object_list:
        game = rec.game
        categories = list(game.categories.values_list('name', flat=True))
        families = list(game.families.values_list('name', flat=True))
        pc_unadj = float(rec.pc_score_unadj or 0.0)
        if pc_range:
            pc_score_val = (pc_unadj - pc_min) / pc_range * 10.0
        else:
            pc_score_val = 0.0
        score_factor_val = float(rec.score_factor or 0.0)
        rows.append({
            'title': game.title,
            'game_id': game.bgg_id,
            'year': game.year,
            'bgg_rank': game.bgg_rank,
            'avg_rating': game.avg_rating,
            'num_voters': game.num_voters,
            'weight': game.weight,
            'weight_votes': game.weight_votes,
            'owned': game.owned,
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
        })

    rows_total = rows_count if rows_count else len(rows)

    start_idx = (page_obj.number - 1) * page_size
    end_idx = start_idx + len(rows)
    start_display = start_idx + 1 if rows_count else 0
    end_display = end_idx
    num_pages = paginator.num_pages or 1

    param_items = []

    def add_param(key, value):
        if value not in (None, ''):
            param_items.append((key, value))

    add_param('q', q)
    add_param('owned_state', owned_state)
    add_param('type', type_filter)
    add_param('playable', playable_state)
    add_param('player_count', player_count_filter)
    add_param('min_year', str(min_year))
    add_param('max_year', str(max_year))
    add_param('min_avg_rating', str(min_avg_rating))
    add_param('max_avg_rating', str(max_avg_rating))
    add_param('min_weight', str(min_weight))
    add_param('max_weight', str(max_weight))
    add_param('min_voters', min_voters_param or '')
    for cat in selected_categories:
        add_param('categories', cat)
    add_param('sort', sort)
    add_param('dir', direction)
    add_param('page', str(page_obj.number))
    add_param('page_size', str(page_size))

    qs_param = '&' + urlencode(param_items, doseq=True) if param_items else ''
    param_items_no_sort = [item for item in param_items if item[0] not in {'sort', 'dir'}]
    qs_nosort = '&' + urlencode(param_items_no_sort, doseq=True) if param_items_no_sort else ''

    pinned_display = [
        'Abstract',
        "Children's Game",
        'Customizable',
        'Family',
        'Party Game',
        'Strategy',
        'Thematic',
        'Wargame',
    ]
    top_cat_pairs = [(name, family_counts.get(name, 0)) for name in pinned_display]
    other_names = sorted(category_counts.keys(), key=lambda x: x.lower())
    other_cat_pairs = [(name, category_counts.get(name, 0)) for name in other_names]
    pinned_norm = {name.lower() for name in pinned_display}
    open_more_categories = any(cat.lower() not in pinned_norm for cat in selected_categories)

    return {
        'rows': rows,
        'rows_count': rows_count,
        'rows_total': rows_total,
        'sort': sort,
        'dir': direction,
        'q': q,
        'owned_state': owned_state,
        'type_filter': type_filter,
        'playable': playable_state,
        'player_count': player_count_filter,
        'min_year': str(min_year),
        'max_year': str(max_year),
        'year_slider_min': year_slider_min,
        'year_slider_max': year_slider_max,
        'min_avg_rating': str(min_avg_rating),
        'max_avg_rating': str(max_avg_rating),
        'min_weight': str(min_weight),
        'max_weight': str(max_weight),
        'min_voters': min_voters_param or '',
        'qs': qs_param,
        'qs_nosort': qs_nosort,
        'page': page_obj.number,
        'page_size': page_size,
        'num_pages': num_pages,
        'start_idx': start_display,
        'end_idx': end_display,
        'top_categories': [name for name, _ in top_cat_pairs],
        'other_categories': [name for name, _ in other_cat_pairs],
        'top_categories_pairs': top_cat_pairs,
        'other_categories_pairs': other_cat_pairs,
        'open_more_categories': open_more_categories,
        'selected_categories': selected_categories,
    }

def games_list(request):
    context = _compute_rows_context(request)
    return render(request, 'games_list.html', context)


def games_rows(request):
    context = _compute_rows_context(request)
    html = render_to_string('partials/games_rows.html', {'rows': context['rows']}, request=request)
    return JsonResponse({
        'html': html,
        'count': context.get('rows_total', context.get('rows_count', len(context['rows']))),
        'page': context['page'],
        'page_size': context['page_size'],
        'num_pages': context['num_pages'],
        'start': context['start_idx'],
        'end': context['end_idx'],
    })


def game_detail(request, bgg_id: str):
    game = get_object_or_404(Game.objects.prefetch_related('categories'), bgg_id=bgg_id)
    pcs = game.player_counts.order_by('count')
    return render(request, 'game_detail.html', {'game': game, 'player_counts': pcs})


def export_csv(request):
    # Export current filtered rows with the same columns as the table
    context = _compute_rows_context(request)
    rows = context['rows']
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="player_count_data.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Score Factor','Game Title','Game ID','Year','BGG Rank','Average Rating','Number of Voters',
        'Weight','Weight Votes','Owned','Type','Categories','Player Count','Best %','Best Votes',
        'Rec. %','Rec. Votes','Not %','Not Votes','Total Votes',
        'Player Count Score (unadjusted)','Player Count Score','Playable'
    ])
    for r in rows:
        writer.writerow([
            r.get('score_factor'),
            r.get('title'),
            r.get('game_id'),
            r.get('year') or 'N/A',
            r.get('bgg_rank') if r.get('bgg_rank') is not None else 'N/A',
            r.get('avg_rating'),
            r.get('num_voters'),
            r.get('weight') if r.get('weight') is not None else 'N/A',
            r.get('weight_votes') if r.get('weight_votes') is not None else 'N/A',
            'Owned' if r.get('owned') else 'Not Owned',
            r.get('type'),
            r.get('categories_str'),
            r.get('player_count'),
            r.get('best_pct'),
            r.get('best_votes'),
            r.get('rec_pct'),
            r.get('rec_votes'),
            r.get('not_pct'),
            r.get('not_votes'),
            r.get('total_votes'),
            r.get('pc_score_unadj'),
            r.get('pc_score'),
            r.get('playable'),
        ])
    return response








