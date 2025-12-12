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
from django.utils import timezone
from django.contrib.auth.decorators import login_required, user_passes_test
from .models import Game, PlayerCountRecommendation, FetchJob, BGGUser, Collection, OwnedGame
from .tasks import run_fetch_top_n, run_fetch_collection, run_refresh
import csv


def home(request):
    total_games = Game.objects.count()
    last_refresh = FetchJob.objects.filter(kind='refresh').order_by('-finished_at', '-created_at').first()
    tracked_users = list(BGGUser.objects.order_by('username').values_list('username', flat=True))
    
    return render(request, 'home.html', {
        'total_games': total_games,
        'last_refresh': last_refresh,
        'tracked_users': tracked_users,
    })

def _refresh_owned_flags(game_ids):
    unique_ids = {gid for gid in (game_ids or []) if gid}
    if not unique_ids:
        return
    remaining = OwnedGame.objects.filter(game_id__in=unique_ids).select_related(
        "collection", "game"
    )
    owners_by_game = {}
    for owned in remaining:
        if not owned.game_id or not owned.collection_id:
            continue
        owners_by_game.setdefault(owned.game_id, set()).add(owned.collection.username)
    games = Game.objects.filter(id__in=unique_ids)
    updates = []
    now = timezone.now()
    for game in games:
        owners = sorted(owners_by_game.get(game.id, []))
        if game.owned != bool(owners) or list(game.owned_by or []) != owners:
            game.owned = bool(owners)
            game.owned_by = owners
            game.updated_at = now
            updates.append(game)
    if updates:
        Game.objects.bulk_update(updates, ["owned", "owned_by", "updated_at"])





@login_required
@user_passes_test(lambda u: u.is_superuser)
def refresh(request):
    saved_users = list(BGGUser.objects.order_by('username').values_list('username', flat=True))
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_user':
            username = (request.POST.get('username') or '').strip()
            if username:
                BGGUser.objects.get_or_create(username=username)
            return redirect('refresh')
        if action == 'delete_user':
            username = (request.POST.get('username') or '').strip()
            if username:
                collection = Collection.objects.filter(username=username).first()
                game_ids = []
                if collection:
                    game_ids = list(OwnedGame.objects.filter(collection=collection).values_list('game_id', flat=True))
                    OwnedGame.objects.filter(collection=collection).delete()
                    collection.delete()
                BGGUser.objects.filter(username=username).delete()
                _refresh_owned_flags(game_ids)
            return redirect('refresh')
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
            run_fetch_top_n(job.id, n, zip_url or None)
            return redirect('job_detail', job_id=job.id)
        if action == 'collection':
            username = (request.POST.get('username') or '').strip()
            job = FetchJob.objects.create(kind='collection', params={'username': username}, status='pending')
            run_fetch_collection(job.id, username)
            return redirect('job_detail', job_id=job.id)
        if action == 'refresh':
            try:
                n = int(request.POST.get('n') or '100')
            except ValueError:
                n = 100
            zip_url = (request.POST.get('zip_url') or '').strip()
            params = {'n': n, 'batch_size': 20}
            if zip_url:
                params['zip_url'] = zip_url
            params['usernames'] = saved_users
            job = FetchJob.objects.create(kind='refresh', params=params, status='pending', total=n)
            run_refresh(job.id, n, saved_users, 20, zip_url or None)
            return redirect('job_detail', job_id=job.id)
    latest_jobs = FetchJob.objects.order_by('-created_at')[:10]
    last_refresh = FetchJob.objects.filter(kind='refresh').order_by('-finished_at', '-created_at').first()
    total_games = Game.objects.count()
    saved_users = list(BGGUser.objects.order_by('username').values_list('username', flat=True))
    return render(request, 'refresh.html', {
        'jobs': latest_jobs,
        'last_refresh': last_refresh,
        'total_games': total_games,
        'users': saved_users,
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
            'created_at': job.created_at.isoformat(),
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
        })

    params = job.params or {}
    if not isinstance(params, dict):
        params = {}
    phases_raw = params.get('phases') if isinstance(params, dict) else {}
    if not isinstance(phases_raw, dict):
        phases_raw = {}
    phase_names = ['top_n', 'collection', 'details', 'cleanup']
    phase_context = {name: (phases_raw.get(name) or {}) for name in phase_names}

    return render(request, 'job_detail.html', {
        'job': job,
        'job_params': params,
        'job_phases': phases_raw,
        'phase_context': phase_context,
        'phase_order': phase_names,
    })


@user_passes_test(lambda u: u.is_superuser)
def cancel_job(request, job_id: int):
    if request.method != 'POST':
        return HttpResponse(status=405)
    job = get_object_or_404(FetchJob, id=job_id)
    if job.status in ('pending', 'running'):
        job.status = 'cancelling'
        job.save(update_fields=['status'])
    return redirect('job_detail', job_id=job.id)


def clear_jobs(request):
    if request.method != 'POST':
        return HttpResponse(status=405)
    FetchJob.objects.all().delete()
    return redirect('home')


def _compute_rows_context(request):
    from .utils import GameFilter, serialize_game_row  # Import inside to avoid circular deps if any

    game_filter = GameFilter(request.GET)
    qs, qs_pre_category, pc_range, pc_min = game_filter.get_queryset()
    
    # Pagination
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
    
    rows = [serialize_game_row(rec, pc_range, pc_min) for rec in page_obj.object_list]
    rows_total = rows_count if rows_count else len(rows)

    start_idx = (page_obj.number - 1) * page_size
    end_idx = start_idx + len(rows)
    start_display = start_idx + 1 if rows_count else 0
    end_display = end_idx
    num_pages = paginator.num_pages or 1
    
    # Categories & Families Counts
    cat_counts, fam_counts = game_filter.get_category_counts(qs_pre_category)

    pinned_display = [
        'Abstract', "Children's Game", 'Customizable', 'Family', 
        'Party Game', 'Strategy', 'Thematic', 'Wargame'
    ]
    top_cat_pairs = [(name, fam_counts.get(name, 0)) for name in pinned_display] # pinned are families mostly? check orig logic
    # Orig logic: pinned looked into family_counts? actually it seemed to check family_counts but names match categories?
    # Wait, in orig logic: 
    # top_cat_pairs = [(name, family_counts.get(name, 0)) for name in pinned_display]
    # NOTE: BGG calls these 'families' (Strategy, Thematic etc) usually, sometimes categories.
    
    other_names = sorted(cat_counts.keys(), key=lambda x: x.lower())
    other_cat_pairs = [(name, cat_counts.get(name, 0)) for name in other_names]
    
    selected_categories = game_filter._get_list('categories')
    pinned_norm = {name.lower() for name in pinned_display}
    open_more_categories = any(cat.lower() not in pinned_norm for cat in selected_categories)

    # Users
    tracked_owner_set = set(BGGUser.objects.values_list('username', flat=True))
    collection_owner_set = set(Collection.objects.values_list('username', flat=True))
    selected_owners = game_filter._get_list('owners')
    owner_usernames = sorted(tracked_owner_set | collection_owner_set | set(selected_owners))

    # Reconstruct query string for pagination/sorting links
    # Logic similar to orig but cleaner to just grab generic params
    # Or reuse existing logic. For safety, let's keep it simple.
    query_dict = request.GET.copy()
    qs_param = '?' + query_dict.urlencode()
    if 'sort' in query_dict: del query_dict['sort']
    if 'dir' in query_dict: del query_dict['dir']
    qs_nosort = '?' + query_dict.urlencode()

    total_games = Game.objects.count()

    return {
        'rows': rows,
        'rows_count': rows_count,
        'rows_total': rows_total,
        'sort': request.GET.get('sort', 'score_factor'),
        'dir': request.GET.get('dir', 'desc'),
        'q': request.GET.get('q', ''),
        'selected_owners': selected_owners,
        'owner_usernames': owner_usernames,
        'type_filter': request.GET.get('type', 'base'),
        'playable': request.GET.get('playable', 'playable'),
        'player_count': request.GET.get('player_count', 'all'),
        'min_year': request.GET.get('min_year') or str(game_filter.year_slider_min),
        'max_year': request.GET.get('max_year') or str(game_filter.year_slider_max),
        'year_slider_min': game_filter.year_slider_min,
        'year_slider_max': game_filter.year_slider_max,
        'min_avg_rating': request.GET.get('min_avg_rating', '0.0'),
        'max_avg_rating': request.GET.get('max_avg_rating', '10.0'),
        'min_weight': request.GET.get('min_weight', '0.0'),
        'max_weight': request.GET.get('max_weight', '5.0'),
        'min_voters': request.GET.get('min_voters', ''),
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
        'total_games': total_games,
    }

def games_list(request):
    context = _compute_rows_context(request)
    context["column_label_pairs"] = [
        ('score_factor', 'Score'),
        ('title', 'Game'),
        ('game_id', 'Game ID'),
        ('year', 'Year'),
        ('bgg_rank', 'BGG Rank'),
        ('avg_rating', 'Avg rating'),
        ('num_voters', 'Voters'),
        ('weight', 'Weight'),
        ('weight_votes', 'Weight votes'),
        ('owners', 'Owners'),
        ('type', 'Type'),
        ('families', 'Families'),
        ('categories', 'Categories'),
        ('player_count', 'Players'),
        ('best_pct', 'Best %'),
        ('best_votes', 'Best votes'),
        ('rec_pct', 'Rec %'),
        ('rec_votes', 'Rec votes'),
        ('not_pct', 'Not %'),
        ('not_votes', 'Not votes'),
        ('total_votes', 'Total votes'),
        ('pc_score', 'PC score'),
        ('playable', 'Playable'),
    ]
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
        'total_games': context.get('total_games'),
    })


def game_detail(request, bgg_id: str):
    game = get_object_or_404(Game.objects.prefetch_related('categories', 'families'), bgg_id=bgg_id)
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
        'Weight','Weight Votes','Owned','Owners','Type','Categories','Player Count','Best %','Best Votes',
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
            r.get('owned_by_str'),
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









