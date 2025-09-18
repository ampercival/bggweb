import threading
import time
from django.utils import timezone
from .models import Game, PlayerCountRecommendation, FetchJob, Collection, OwnedGame
from django.db import transaction
from .services.bgg_client import BGGClient


def _merge_owned_flag(games_map: dict, owned_map: dict) -> dict:
    out = dict(games_map)
    for gid, data in owned_map.items():
        if gid in out:
            out[gid]['Owned'] = 'Owned'
        else:
            out[gid] = data
    return out


def _persist_games_and_counts(games_map: dict, details_map: dict, player_counts: dict):
    # Upsert games
    for gid, g in games_map.items():
        det = details_map.get(gid, {})
        rank_val = det.get('BGG Rank')
        obj, _ = Game.objects.update_or_create(
            bgg_id=gid,
            defaults={
                'title': g.get('Game Title') or '',
                'type': g.get('Type') or 'Base Game',
                'year': det.get('Year'),
                'avg_rating': g.get('Average Rating'),
                'num_voters': g.get('Number of Voters'),
                'weight': det.get('Weight'),
                'weight_votes': det.get('Weight Votes'),
                'bgg_rank': rank_val,
                'owned': (g.get('Owned') == 'Owned'),
            }
        )
        # Categories
        cat_names = det.get('Categories') or []
        if isinstance(cat_names, list):
            from .models import Category
            cat_objs = []
            for name in sorted(set([c for c in cat_names if c])):
                cat_obj, _ = Category.objects.get_or_create(name=name)
                cat_objs.append(cat_obj)
            # replace existing relations
            if cat_objs:
                obj.categories.set(cat_objs)
            else:
                obj.categories.clear()
        # Families
        fam_names = det.get('Families') or []
        if isinstance(fam_names, list):
            from .models import Family
            fam_objs = []
            for name in sorted(set([f for f in fam_names if f])):
                fam_obj, _ = Family.objects.get_or_create(name=name)
                fam_objs.append(fam_obj)
            if fam_objs:
                obj.families.set(fam_objs)
            else:
                obj.families.clear()
        # Player counts
        pc = player_counts.get(gid, {})
        for k, v in pc.items():
            try:
                count_int = int(k)
            except Exception:
                continue
            PlayerCountRecommendation.objects.update_or_create(
                game=obj,
                count=count_int,
                defaults={
                    'best_pct': v.get('Best %', 0.0),
                    'best_votes': v.get('Best Votes', 0),
                    'rec_pct': v.get('Recommended %', 0.0),
                    'rec_votes': v.get('Recommended Votes', 0),
                    'notrec_pct': v.get('Not Recommended %', 0.0),
                    'notrec_votes': v.get('Not Recommended Votes', 0),
                    'vote_count': v.get('Vote Count', 0),
                }
            )


def run_fetch_top_n(job_id: int, n: int):
    job = FetchJob.objects.get(id=job_id)
    job.status = 'running'
    job.progress = 0
    job.total = n
    job.save(update_fields=['status', 'progress', 'total'])

    client = BGGClient()
    try:
        # 1) scrape approximate top N
        base_map = client.fetch_top_games_scrape(n)
        ids = list(base_map.keys())
        job.progress = min(len(ids), n)
        job.save(update_fields=['progress'])

        # 2) details in batches
        details, pcounts = client.fetch_details_batches(ids, batch_size=20)

        # 3) atomic replace of all game-related data for a full refresh
        with transaction.atomic():
            # Deleting games cascades to player-count rows and OwnedGame links
            Game.objects.all().delete()
            _persist_games_and_counts(base_map, details, pcounts)

        job.status = 'done'
        job.finished_at = timezone.now()
        job.progress = job.total
        job.save(update_fields=['status', 'finished_at', 'progress'])
    except Exception as e:
        job.status = 'error'
        job.error = str(e)
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'error', 'finished_at'])


def run_fetch_collection(job_id: int, username: str):
    job = FetchJob.objects.get(id=job_id)
    job.status = 'running'
    job.progress = 0
    job.save(update_fields=['status', 'progress'])

    client = BGGClient()
    try:
        # 1) collection
        owned_map = client.fetch_owned_collection(username)
        ids = list(owned_map.keys())
        job.total = len(ids)
        job.progress = len(ids)
        job.save(update_fields=['total', 'progress'])

        # 2) details
        details, pcounts = client.fetch_details_batches(ids, batch_size=20)

        # 3) persist
        _persist_games_and_counts(owned_map, details, pcounts)

        # 4) link collection object
        coll, _ = Collection.objects.get_or_create(username=username)
        # ensure M2M through OwnedGame contains all
        from .models import Game as GameModel
        for gid in ids:
            try:
                gobj = GameModel.objects.get(bgg_id=gid)
            except GameModel.DoesNotExist:
                continue
            OwnedGame.objects.get_or_create(collection=coll, game=gobj)

        job.status = 'done'
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'finished_at'])
    except Exception as e:
        job.status = 'error'
        job.error = str(e)
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'error', 'finished_at'])


from django.db import close_old_connections


def start_background(target, *args, **kwargs):
    def _runner():
        # Ensure a fresh DB connection for this thread
        close_old_connections()
        try:
            target(*args, **kwargs)
        finally:
            close_old_connections()
    th = threading.Thread(target=_runner, daemon=True)
    th.start()
    return th


def run_refresh(job_id: int, n: int, username: str | None, batch_size: int):
    job = FetchJob.objects.get(id=job_id)
    job.status = 'running'
    job.progress = 0
    job.total = n
    # Initialize phase tracking in params
    params = job.params or {}
    params['batch_size'] = batch_size
    params['phases'] = {
        'top_n': {
            'status': 'running', 'progress': 0, 'total': n,
            'started_at': timezone.now().isoformat()
        },
        'collection': (
            {
                'status': 'running', 'progress': 0, 'total': 2, 'items': 0,
                'started_at': timezone.now().isoformat()
            } if username else {'status': 'skipped'}
        ),
        'details': {'status': 'pending', 'progress': 0, 'total': 0, 'batch': batch_size},
        # New phase to represent DB apply/commit work
        'apply': {'status': 'pending', 'progress': 0, 'total': 0},
    }
    job.params = params
    job.save(update_fields=['status', 'progress', 'total', 'params'])

    client = BGGClient()
    try:
        # 1) Top N scrape
        last_save = 0.0

        def save_throttled(fields):
            nonlocal last_save
            now = time.time()
            if now - last_save >= 0.5:
                job.save(update_fields=fields)
                last_save = now

        def on_top_progress(**kw):
            params = job.params or {}
            ph = params.get('phases', {}).get('top_n', {})
            ph['progress'] = kw.get('progress', ph.get('progress', 0))
            ph['total'] = kw.get('total', ph.get('total', n))
            ph['updated_at'] = timezone.now().isoformat()
            params['phases']['top_n'] = ph
            job.params = params
            job.progress = ph['progress']
            save_throttled(['params', 'progress'])

        top_map = client.fetch_top_games_scrape(n, on_progress=on_top_progress)
        top_ids = list(top_map.keys())
        # mark top_n phase done
        params = job.params or {}
        params['phases']['top_n'].update({
            'status': 'done', 'progress': min(len(top_ids), n),
            'finished_at': timezone.now().isoformat()
        })
        job.params = params
        job.progress = min(len(top_ids), n)
        job.save(update_fields=['params', 'progress'])

        # 2) Owned collection (optional)
        owned_map = {}
        if username:
            def on_coll_progress(**kw):
                params = job.params or {}
                ph = params.get('phases', {}).get('collection', {})
                if 'progress' in kw:
                    ph['progress'] = kw['progress']
                if 'total' in kw:
                    ph['total'] = kw['total']
                if 'items' in kw:
                    ph['items'] = kw['items']
                ph['updated_at'] = timezone.now().isoformat()
                params['phases']['collection'] = ph
                job.params = params
                save_throttled(['params'])

            owned_map = client.fetch_owned_collection(username, on_progress=on_coll_progress)
            # mark collection done
            params = job.params or {}
            if 'collection' in params.get('phases', {}):
                params['phases']['collection'].update({
                    'status': 'done', 'items': len(owned_map),
                    'finished_at': timezone.now().isoformat()
                })
                job.params = params
                job.save(update_fields=['params'])

        # 3) Merge owned flag into Top N only (do not include owned games outside Top N)
        combined = dict(top_map)
        if username:
            for gid in owned_map.keys():
                if gid in combined:
                    combined[gid]['Owned'] = 'Owned'
        all_ids = list(combined.keys())
        job.total = len(all_ids)
        job.progress = len(top_ids)  # keep overall progress incremental
        params = job.params or {}
        params['phases']['details'].update({
            'status': 'running', 'progress': 0, 'total': len(all_ids), 'batch': batch_size,
            'started_at': timezone.now().isoformat()
        })
        job.params = params
        job.save(update_fields=['total', 'progress', 'params'])

        # 4) Fetch details + polls
        def on_details_progress(**kw):
            params = job.params or {}
            ph = params.get('phases', {}).get('details', {})
            if 'processed' in kw:
                ph['progress'] = kw['processed']
            if 'total' in kw:
                ph['total'] = kw['total']
            if 'batch' in kw:
                ph['batch'] = kw['batch']
            ph['updated_at'] = timezone.now().isoformat()
            params['phases']['details'] = ph
            job.params = params
            # map overall progress roughly as details progress
            job.progress = ph.get('progress', job.progress)
            job.total = ph.get('total', job.total)
            save_throttled(['params', 'progress', 'total'])

        details, pcounts = client.fetch_details_batches(all_ids, batch_size=20, on_progress=on_details_progress)

        # Mark details done before starting apply phase
        params = job.params or {}
        if 'details' in params.get('phases', {}):
            params['phases']['details'].update({
                'status': 'done', 'progress': len(all_ids), 'total': len(all_ids),
                'batch': batch_size, 'finished_at': timezone.now().isoformat()
            })
            job.params = params
            job.save(update_fields=['params'])

        # 5) Full refresh (atomic)
        # Start apply phase (progress may not update during atomic transaction)
        params = job.params or {}
        if 'apply' in params.get('phases', {}):
            params['phases']['apply'].update({
                'status': 'running', 'progress': 0, 'total': len(all_ids),
                'started_at': timezone.now().isoformat()
            })
            job.params = params
            job.save(update_fields=['params'])
        with transaction.atomic():
            Game.objects.all().delete()
            _persist_games_and_counts(combined, details, pcounts)
            if username:
                coll, _ = Collection.objects.get_or_create(username=username)
                from .models import Game as GameModel
                for gid in combined.keys():
                    if gid not in owned_map:
                        continue
                    try:
                        gobj = GameModel.objects.get(bgg_id=gid)
                        OwnedGame.objects.get_or_create(collection=coll, game=gobj)
                    except GameModel.DoesNotExist:
                        continue
        # Mark apply done
        params = job.params or {}
        if 'apply' in params.get('phases', {}):
            params['phases']['apply'].update({
                'status': 'done', 'progress': len(all_ids), 'total': len(all_ids),
                'finished_at': timezone.now().isoformat()
            })
            job.params = params
        job.status = 'done'
        job.finished_at = timezone.now()
        job.progress = job.total
        job.save(update_fields=['status', 'finished_at', 'progress', 'params'])
    except Exception as e:
        job.status = 'error'
        job.error = str(e)
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'error', 'finished_at'])
