from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.db.models import Prefetch, F
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
            job = FetchJob.objects.create(kind='top_n', params={'n': n}, status='pending', total=n)
            start_background(run_fetch_top_n, job.id, n)
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
            job = FetchJob.objects.create(kind='refresh', params={'n': n, 'username': username, 'batch_size': 20}, status='pending', total=n)
            start_background(run_refresh, job.id, n, username, 20)
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
    # Build per-player-count rows similar to BGG_DataDisplay and return context dict
    sort = request.GET.get('sort', 'score_factor')
    direction = request.GET.get('dir', 'desc')

    # Filters (mirror PyQt)
    q = request.GET.get('q', '')
    owned_state = request.GET.get('owned_state', 'all')  # all|owned|not
    type_filter = request.GET.get('type', 'base')  # default to base games
    playable_state = request.GET.get('playable', 'playable')  # default Playable
    player_count_filter = request.GET.get('player_count', 'all')  # all|1..7|8plus
    min_year_param = request.GET.get('min_year')
    max_year_param = request.GET.get('max_year')
    min_avg_rating_param = request.GET.get('min_avg_rating')
    max_avg_rating_param = request.GET.get('max_avg_rating')
    min_weight_param = request.GET.get('min_weight')
    max_weight_param = request.GET.get('max_weight')
    min_voters_param = request.GET.get('min_voters')

    def to_int(val):
        try:
            return int(val) if val not in (None, '') else None
        except ValueError:
            return None

    def to_float(val):
        try:
            return float(val) if val not in (None, '') else None
        except ValueError:
            return None

    # Year sliders always active with sensible defaults
    year_slider_min = 1900
    year_slider_max = datetime.now().year
    min_year = to_int(min_year_param)
    max_year = to_int(max_year_param)
    if min_year is None:
        min_year = year_slider_min
    if max_year is None:
        max_year = year_slider_max
    # Always-on ranges with sensible defaults if not provided
    min_avg_rating = to_float(min_avg_rating_param)
    max_avg_rating = to_float(max_avg_rating_param)
    if min_avg_rating is None:
        min_avg_rating = 0.0
    if max_avg_rating is None:
        max_avg_rating = 10.0
    min_weight = to_float(min_weight_param)
    max_weight = to_float(max_weight_param)
    if min_weight is None:
        min_weight = 0.0
    if max_weight is None:
        max_weight = 5.0
    min_voters = to_int(min_voters_param)

    # Load all PCR with related games, categories, and families to avoid N+1
    pcs = (
        PlayerCountRecommendation.objects
        .select_related('game')
        .prefetch_related('game__categories', 'game__families')
        .all()
    )

    rows = []
    from collections import defaultdict
    category_counts = defaultdict(int)
    family_counts = defaultdict(int)
    unadj_scores = []
    for pc in pcs:
        g = pc.game
        best_pct = pc.best_pct or 0.0
        rec_pct = pc.rec_pct or 0.0
        not_pct = pc.notrec_pct or 0.0
        pc_unadj = round(best_pct * 3 + rec_pct * 2 + (not_pct * -2), 1)
        unadj_scores.append(pc_unadj)
        cats = [c.name for c in getattr(g, 'categories').all()] if hasattr(g, 'categories') else []
        fams = [f.name for f in getattr(g, 'families').all()] if hasattr(g, 'families') else []
        # category counting happens after non-category filters
        rows.append({
            'title': g.title,
            'game_id': g.bgg_id,
            'year': g.year,
            'bgg_rank': g.bgg_rank,
            'avg_rating': g.avg_rating,
            'num_voters': g.num_voters,
            'weight': g.weight,
            'weight_votes': g.weight_votes,
            'owned': g.owned,
            'type': g.type,
            'categories': cats,
            'categories_str': ', '.join(sorted(cats)),
            'families': fams,
            'player_count': pc.count,
            'best_pct': best_pct,
            'best_votes': pc.best_votes,
            'rec_pct': rec_pct,
            'rec_votes': pc.rec_votes,
            'not_pct': not_pct,
            'not_votes': pc.notrec_votes,
            'total_votes': pc.vote_count,
            'pc_score_unadj': pc_unadj,
        })

    # Normalize Player Count Score to 0-10 across all rows
    if unadj_scores:
        mn = min(unadj_scores)
        mx = max(unadj_scores)
    else:
        mn = mx = 0
    for r in rows:
        if mx != mn:
            r['pc_score'] = round(((r['pc_score_unadj'] - mn) / (mx - mn)) * 10, 2)
        else:
            r['pc_score'] = 0.0
        # Playable threshold 150 per original script
        r['playable'] = 'Playable' if r['pc_score_unadj'] >= 150 else 'Not Playable'
        # Score Factor = (avg_rating*3 + pc_score*1)/4
        ar = r['avg_rating'] or 0.0
        r['score_factor'] = round(((ar * 3) + (r['pc_score'] * 1)) / 4, 3)

    # Apply filters in two passes: first without categories (to compute frequencies), then categories filter
    pre_category = []
    filtered = []
    q_lower = q.lower()
    selected_categories = request.GET.getlist('categories')
    for r in rows:
        # Text
        if q and q_lower not in (r['title'] or '').lower():
            continue
        # Owned
        if owned_state == 'owned' and not r['owned']:
            continue
        if owned_state == 'not' and r['owned']:
            continue
        # Type
        if type_filter == 'base' and (r['type'] or '') != 'Base Game':
            continue
        if type_filter == 'expansion' and (r['type'] or '') != 'Expansion':
            continue
        # Playable
        if playable_state == 'playable' and r['playable'] != 'Playable':
            continue
        if playable_state == 'not' and r['playable'] != 'Not Playable':
            continue
        # Player count
        pc = r['player_count'] or 0
        if player_count_filter.isdigit():
            if pc != int(player_count_filter):
                continue
        elif player_count_filter == '8plus':
            if pc < 8:
                continue
        # Year range (year stored as str); keep rows with unknown year only if full-range is selected
        y_val = None
        try:
            y_val = int(r['year']) if r['year'] and str(r['year']).isdigit() else None
        except Exception:
            y_val = None
        strict_year = (min_year > year_slider_min) or (max_year < year_slider_max)
        if strict_year and y_val is None:
            continue
        if y_val is not None and y_val < min_year:
            continue
        if y_val is not None and y_val > max_year:
            continue
        # Avg rating range (always active)
        ar = r['avg_rating'] if r['avg_rating'] is not None else None
        if ar is None or ar < min_avg_rating or ar > max_avg_rating:
            continue
        # Weight range (always active)
        wt = r['weight'] if r['weight'] is not None else None
        if wt is None or wt < min_weight or wt > max_weight:
            continue
        # Min voters
        if min_voters is not None and (r['num_voters'] or 0) < min_voters:
            continue
        pre_category.append(r)
        for c in (r.get('categories') or []):
            category_counts[c] += 1
        for f in (r.get('families') or []):
            family_counts[f] += 1

    if selected_categories:
        for r in pre_category:
            if any((c in selected_categories) for c in (r.get('categories') or [])):
                filtered.append(r)
    else:
        filtered = pre_category

    # Sorting in Python for both model and computed fields
    def sort_key(r):
        val = r.get(sort)
        if sort in {'bgg_rank', 'avg_rating', 'num_voters', 'weight', 'weight_votes', 'player_count', 'best_pct', 'best_votes', 'rec_pct', 'rec_votes', 'not_pct', 'not_votes', 'total_votes', 'pc_score_unadj', 'pc_score', 'score_factor'}:
            return (val is None, val)
        if sort == 'owned':
            return (val is True,)
        return (str(val).lower() if val is not None else 'zzz',)

    filtered.sort(key=sort_key, reverse=(direction == 'desc'))

    # Pagination
    def to_page_int(val, default):
        try:
            v = int(val)
            return v if v > 0 else default
        except Exception:
            return default
    page = to_page_int(request.GET.get('page'), 1)
    page_size = to_page_int(request.GET.get('page_size'), 50)
    if page_size > 1000:
        page_size = 1000
    total_rows = len(filtered)
    num_pages = max(1, (total_rows + page_size - 1) // page_size)
    if page > num_pages:
        page = num_pages
    start_idx = (page - 1) * page_size
    end_idx = min(start_idx + page_size, total_rows)
    rows_page = filtered[start_idx:end_idx]

    # Build qs to preserve filters with multi-value categories
    param_items = []
    def add(k, v):
        if v not in (None, ''):
            param_items.append((k, v))
    add('q', q)
    add('owned_state', owned_state)
    add('type', type_filter)
    add('playable', playable_state)
    add('player_count', player_count_filter)
    add('min_year', str(min_year))
    add('max_year', str(max_year))
    add('min_avg_rating', str(min_avg_rating))
    add('max_avg_rating', str(max_avg_rating))
    add('min_weight', str(min_weight))
    add('max_weight', str(max_weight))
    add('min_voters', min_voters_param or '')
    for c in selected_categories:
        add('categories', c)
    add('sort', sort)
    add('dir', direction)
    add('page', str(page))
    add('page_size', str(page_size))
    qs = '&' + urlencode(param_items, doseq=True) if param_items else ''
    # Also build a qs without sort/dir for header links so toggled sort wins
    param_items_no_sort = [it for it in param_items if it[0] not in ('sort', 'dir')]
    qs_nosort = '&' + urlencode(param_items_no_sort, doseq=True) if param_items_no_sort else ''

    # Build pinned categories (always visible) and the rest (collapsed)
    pinned_display = [
        "Abstract Game",
        "Children's Game",
        "Customizable Game",
        "Family Game",
        "Party Game",
        "Strategy Game",
        "Thematic Game",
        "Wargame",
    ]
    def norm(s: str) -> str:
        try:
            return (s or "").replace("’", "'").strip().lower()
        except Exception:
            return ""
    # Sum counts by normalized name to handle apostrophe variants
    from collections import defaultdict as _dd
    norm_counts = _dd(int)
    for name, cnt in category_counts.items():
        norm_counts[norm(name)] += cnt
    # Pinned pairs in fixed order with counts (0 if absent)
    top_cat_pairs = [(disp, norm_counts.get(norm(disp), 0)) for disp in pinned_display]
    pinned_norm_set = {norm(d) for d in pinned_display}
    # Other categories: present names excluding pinned, alphabetical
    other_names = [name for name in category_counts.keys() if norm(name) not in pinned_norm_set]
    other_names.sort(key=lambda x: x.lower())
    other_cat_pairs = [(name, category_counts.get(name, 0)) for name in other_names]

    # Override pinned logic to use BGG family groups for pinned, and categories for collapsed
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
    # Build pinned pairs from family_counts (persisted families)
    top_cat_pairs = [(disp, family_counts.get(disp, 0)) for disp in pinned_display]
    # Collapsed list is categories (boardgamecategory) alphabetically
    other_names = sorted(category_counts.keys(), key=lambda x: x.lower())
    other_cat_pairs = [(name, category_counts.get(name, 0)) for name in other_names]
    # Open collapsed if any selected category is outside pinned families
    pinned_norm = {d.lower() for d in pinned_display}
    open_more_categories = any((c.lower() not in pinned_norm) for c in selected_categories)

    return {
        'rows': rows_page,
        'rows_count': total_rows,
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
        'qs': qs,
        'qs_nosort': qs_nosort,
        'page': page,
        'page_size': page_size,
        'num_pages': num_pages,
        'start_idx': start_idx + 1 if total_rows else 0,
        'end_idx': end_idx,
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
        'count': context.get('rows_count', len(context['rows'])),
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
