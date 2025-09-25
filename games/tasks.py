import threading
import time
import logging
from collections import defaultdict
from django.db import close_old_connections
from django.utils import timezone

from .models import (
    Category,
    Collection,
    Family,
    FetchJob,
    Game,
    OwnedGame,
    PlayerCountRecommendation,
)
from .services.bgg_client import BGGClient

log = logging.getLogger(__name__)


def _to_float(value):
    try:
        if value in (None, "", "null"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        if value in (None, "", "null"):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_type(raw) -> str:
    text = str(raw or "Base Game").lower()
    return "Expansion" if text.startswith("exp") else "Base Game"


def _collect_vocab(model, names):
    filtered = sorted({name for name in names if name})
    if not filtered:
        return {}
    existing = model.objects.in_bulk(filtered, field_name="name")
    missing = [model(name=name) for name in filtered if name not in existing]
    if missing:
        model.objects.bulk_create(missing, ignore_conflicts=True)
        existing = model.objects.in_bulk(filtered, field_name="name")
    return existing


def _sync_catalog(
    games_map,
    details_map,
    player_counts,
    *,
    collection_owned_map=None,
    prune=False,
    progress_callback=None,
    progress_total=None,
):
    collection_owned_map = collection_owned_map or {}
    games_map = {str(k): v for k, v in (games_map or {}).items()}
    details_map = {str(k): v for k, v in (details_map or {}).items()}
    player_counts = {str(k): v for k, v in (player_counts or {}).items()}

    normalized_collections = {}
    for username, ids in collection_owned_map.items():
        if not username:
            continue
        normalized_collections[username] = {str(gid) for gid in ids if gid}
    collection_owned_map = normalized_collections

    owners_input_by_gid = defaultdict(set)
    for username, ids in collection_owned_map.items():
        for gid in ids:
            owners_input_by_gid[gid].add(username)

    desired_ids = list(games_map.keys())
    now = timezone.now()

    progress_target = progress_total if progress_total is not None else len(desired_ids)
    track_progress = bool(progress_callback) and progress_target > 0

    prune_qs = None
    prune_units = 0
    games_units = 0
    relations_units = 0
    player_units = 0
    owned_units = 0
    total_units = 0
    completed_units = 0
    last_progress = -1

    def report_units(_add):
        return

    if track_progress:
        if prune:
            prune_qs = Game.objects.exclude(bgg_id__in=desired_ids)
            prune_units = prune_qs.count()
        games_units = len(desired_ids)
        relations_units = len(desired_ids)
        player_units = sum(len(player_counts.get(gid, {})) for gid in desired_ids)
        if player_units == 0 and games_units:
            player_units = games_units
        owned_units = sum(len(ids) for ids in collection_owned_map.values())
        total_units = prune_units + games_units + relations_units + player_units + owned_units
        if total_units <= 0:
            total_units = 1

        def report_units(add_units):
            nonlocal completed_units, last_progress
            if add_units <= 0:
                return
            completed_units += add_units
            progress_value = min(
                progress_target,
                max(0, int(round(progress_target * completed_units / total_units))),
            )
            if progress_value > last_progress:
                progress_callback(progress_value)
                last_progress = progress_value

    if prune:
        if prune_qs is not None:
            prune_qs.delete()
            report_units(prune_units)
        else:
            Game.objects.exclude(bgg_id__in=desired_ids).delete()

    existing_games = Game.objects.in_bulk(desired_ids, field_name="bgg_id")
    games_to_create = []
    games_to_update = []
    for gid in desired_ids:
        info = games_map[gid]
        detail = details_map.get(gid, {})
        game_type = _normalize_type(info.get("Type"))
        year = detail.get("Year")
        if year is not None:
            year = str(year)
        avg_rating = _to_float(info.get("Average Rating"))
        num_voters = _to_int(info.get("Number of Voters"))
        weight = _to_float(detail.get("Weight"))
        weight_votes = _to_int(detail.get("Weight Votes"))
        bgg_rank = _to_int(detail.get("BGG Rank"))
        owners_for_gid = sorted(owners_input_by_gid.get(gid, set()))

        if gid in existing_games:
            game = existing_games[gid]
            game.title = info.get("Game Title") or game.title
            game.type = game_type
            game.year = year
            game.avg_rating = avg_rating
            game.num_voters = num_voters
            game.weight = weight
            game.weight_votes = weight_votes
            game.bgg_rank = bgg_rank
            game.updated_at = now
            games_to_update.append(game)
        else:
            games_to_create.append(
                Game(
                    bgg_id=gid,
                    title=info.get("Game Title") or "",
                    type=game_type,
                    year=year,
                    avg_rating=avg_rating,
                    num_voters=num_voters,
                    weight=weight,
                    weight_votes=weight_votes,
                    bgg_rank=bgg_rank,
                    owned=bool(owners_for_gid),
                    owned_by=owners_for_gid,
                    created_at=now,
                    updated_at=now,
                )
            )

    if games_to_create:
        Game.objects.bulk_create(games_to_create, batch_size=500)
    if games_to_update:
        Game.objects.bulk_update(
            games_to_update,
            [
                "title",
                "type",
                "year",
                "avg_rating",
                "num_voters",
                "weight",
                "weight_votes",
                "bgg_rank",
                "updated_at",
            ],
            batch_size=500,
        )

    game_objs = Game.objects.in_bulk(desired_ids, field_name="bgg_id")
    report_units(games_units)

    category_names = set()
    family_names = set()
    for gid in desired_ids:
        detail = details_map.get(gid, {})
        for name in detail.get("Categories") or []:
            if name:
                category_names.add(name)
        for name in detail.get("Families") or []:
            if name:
                family_names.add(name)

    category_map = _collect_vocab(Category, category_names)
    family_map = _collect_vocab(Family, family_names)

    for gid in desired_ids:
        game = game_objs.get(gid)
        if not game:
            continue
        detail = details_map.get(gid, {})
        cat_objs = [category_map[name] for name in (detail.get("Categories") or []) if name in category_map]
        fam_objs = [family_map[name] for name in (detail.get("Families") or []) if name in family_map]
        game.categories.set(cat_objs)
        game.families.set(fam_objs)

    report_units(relations_units)

    existing_recs = PlayerCountRecommendation.objects.filter(
        game__bgg_id__in=desired_ids
    ).select_related("game")
    rec_map = {}
    for rec in existing_recs:
        rec_map.setdefault(rec.game.bgg_id, {})[rec.count] = rec

    recs_to_update = []
    recs_to_create = []
    recs_to_delete = []

    for gid in desired_ids:
        counts = player_counts.get(gid, {}) or {}
        game = game_objs.get(gid)
        if not game:
            continue
        seen_counts = set()
        for count, data in counts.items():
            count = int(count)
            best_pct = _to_float(data.get("Best %")) or 0.0
            best_votes = _to_int(data.get("Best Votes")) or 0
            rec_pct = _to_float(data.get("Rec. %")) or 0.0
            rec_votes = _to_int(data.get("Rec. Votes")) or 0
            notrec_pct = _to_float(data.get("Not %")) or 0.0
            notrec_votes = _to_int(data.get("Not Votes")) or 0
            vote_count = _to_int(data.get("Total Votes")) or 0
            seen_counts.add(count)
            existing = rec_map.get(gid, {}).get(count)
            if existing:
                existing.best_pct = best_pct
                existing.best_votes = best_votes
                existing.rec_pct = rec_pct
                existing.rec_votes = rec_votes
                existing.notrec_pct = notrec_pct
                existing.notrec_votes = notrec_votes
                existing.vote_count = vote_count
                recs_to_update.append(existing)
            else:
                recs_to_create.append(
                    PlayerCountRecommendation(
                        game=game,
                        count=count,
                        best_pct=best_pct,
                        best_votes=best_votes,
                        rec_pct=rec_pct,
                        rec_votes=rec_votes,
                        notrec_pct=notrec_pct,
                        notrec_votes=notrec_votes,
                        vote_count=vote_count,
                    )
                )
        existing_for_game = rec_map.get(gid, {})
        for count, rec_obj in existing_for_game.items():
            if count not in seen_counts:
                recs_to_delete.append(rec_obj.id)

    if recs_to_delete:
        PlayerCountRecommendation.objects.filter(id__in=recs_to_delete).delete()
    if recs_to_update:
        PlayerCountRecommendation.objects.bulk_update(
            recs_to_update,
            [
                "best_pct",
                "best_votes",
                "rec_pct",
                "rec_votes",
                "notrec_pct",
                "notrec_votes",
                "vote_count",
            ],
            batch_size=500,
        )
    if recs_to_create:
        PlayerCountRecommendation.objects.bulk_create(recs_to_create, batch_size=500)

    report_units(player_units)

    if collection_owned_map:
        for username, target_ids in collection_owned_map.items():
            coll, _ = Collection.objects.get_or_create(username=username)
            target_ids = set(target_ids)
            missing_ids = [gid for gid in target_ids if gid not in game_objs]
            if missing_ids:
                game_objs.update(Game.objects.in_bulk(missing_ids, field_name="bgg_id"))
            existing_owned = OwnedGame.objects.filter(collection=coll).select_related("game")
            existing_owned_map = {og.game.bgg_id: og for og in existing_owned if og.game_id}
            owned_to_create = []
            for gid in target_ids:
                if gid not in existing_owned_map:
                    game = game_objs.get(gid)
                    if game:
                        owned_to_create.append(OwnedGame(collection=coll, game=game))
            if owned_to_create:
                OwnedGame.objects.bulk_create(owned_to_create, ignore_conflicts=True)
            to_remove = [og.id for gid, og in existing_owned_map.items() if gid not in target_ids]
            if to_remove:
                OwnedGame.objects.filter(id__in=to_remove).delete()
        report_units(owned_units)

    relevant_ids = set(desired_ids)
    relevant_ids.update(owners_input_by_gid.keys())
    if relevant_ids:
        missing_for_relevant = [gid for gid in relevant_ids if gid not in game_objs]
        if missing_for_relevant:
            game_objs.update(Game.objects.in_bulk(missing_for_relevant, field_name="bgg_id"))
        owner_qs = OwnedGame.objects.filter(game__bgg_id__in=relevant_ids).select_related("collection", "game")
        owners_by_game_actual = defaultdict(list)
        for owned in owner_qs:
            if not owned.collection_id or not owned.game_id:
                continue
            owners_by_game_actual[owned.game.bgg_id].append(owned.collection.username)
        games_to_owner_update = []
        for gid in relevant_ids:
            game = game_objs.get(gid)
            if not game:
                continue
            owners = sorted(set(owners_by_game_actual.get(gid, [])))
            existing = list(game.owned_by or [])
            if game.owned != bool(owners) or existing != owners:
                game.owned = bool(owners)
                game.owned_by = owners
                game.updated_at = now
                games_to_owner_update.append(game)
        if games_to_owner_update:
            Game.objects.bulk_update(games_to_owner_update, ["owned", "owned_by", "updated_at"], batch_size=500)

    if track_progress and progress_target > 0 and last_progress < progress_target:
        progress_callback(progress_target)




def _purge_untracked_collections(active_usernames):
    normalized = {(u or '').strip() for u in (active_usernames or []) if (u or '').strip()}
    if normalized:
        purge_qs = Collection.objects.exclude(username__in=normalized)
    else:
        purge_qs = Collection.objects.all()
    if not purge_qs.exists():
        return
    collection_ids = list(purge_qs.values_list('id', flat=True))
    owned_qs = OwnedGame.objects.filter(collection_id__in=collection_ids).select_related('collection', 'game')
    affected_game_ids = set()
    for owned in owned_qs:
        if owned.game_id:
            affected_game_ids.add(owned.game_id)
    owned_qs.delete()
    purge_qs.delete()
    if not affected_game_ids:
        return
    remaining_owned = OwnedGame.objects.filter(game_id__in=affected_game_ids).select_related('collection', 'game')
    owners_by_game = defaultdict(list)
    for owned in remaining_owned:
        if owned.collection_id and owned.collection.username:
            owners_by_game[owned.game_id].append(owned.collection.username)
    games_to_update = []
    now = timezone.now()
    for game in Game.objects.filter(id__in=affected_game_ids):
        owners = sorted(set(owners_by_game.get(game.id, [])))
        if game.owned != bool(owners) or list(game.owned_by or []) != owners:
            game.owned = bool(owners)
            game.owned_by = owners
            game.updated_at = now
            games_to_update.append(game)
    if games_to_update:
        Game.objects.bulk_update(games_to_update, ['owned', 'owned_by', 'updated_at'])



def _build_owners_lookup(collection_owned_map):
    owners = defaultdict(set)
    for username, ids in (collection_owned_map or {}).items():
        if not username:
            continue
        for gid in ids:
            if not gid:
                continue
            owners[str(gid)].add(username)
    return owners


def _ensure_collections(usernames):
    cache = {}
    normalized = {(u or '').strip() for u in (usernames or []) if (u or '').strip()}
    for username in sorted(normalized):
        coll, _ = Collection.objects.get_or_create(username=username)
        cache[username] = coll
    return cache


def _sync_refresh_chunk(
    chunk_ids,
    games_map,
    details_map,
    player_counts_map,
    owners_lookup,
    collection_cache,
):
    if not chunk_ids:
        return

    games_map = {str(k): v for k, v in (games_map or {}).items()}
    details_map = {str(k): v for k, v in (details_map or {}).items()}
    player_counts_map = {str(k): v for k, v in (player_counts_map or {}).items()}
    owners_lookup = owners_lookup or {}

    normalized_ids = [str(gid) for gid in chunk_ids]
    normalized_ids = [gid for gid in normalized_ids if gid in games_map or gid in details_map]
    if not normalized_ids:
        return

    now = timezone.now()

    existing_games = Game.objects.in_bulk(normalized_ids, field_name='bgg_id')
    games_to_create = []
    games_to_update = []

    for gid in normalized_ids:
        info = games_map.get(gid) or {}
        detail = details_map.get(gid)
        if not info and not detail:
            continue

        game_type = _normalize_type(info.get('Type'))
        avg_rating = _to_float(info.get('Average Rating'))
        num_voters = _to_int(info.get('Number of Voters'))

        year_value = detail.get('Year') if detail else None
        if year_value is not None:
            year_value = str(year_value)

        weight = _to_float(detail.get('Weight')) if detail else None
        weight_votes = _to_int(detail.get('Weight Votes')) if detail else None

        bgg_rank = _to_int((detail or {}).get('BGG Rank'))
        if bgg_rank is None:
            bgg_rank = _to_int(info.get('BGG Rank'))

        owners_for_gid = sorted(owners_lookup.get(gid, set()))

        if gid in existing_games:
            game = existing_games[gid]
            title_value = info.get('Game Title')
            if title_value:
                game.title = title_value
            game.type = game_type
            game.avg_rating = avg_rating
            game.num_voters = num_voters
            if detail is not None:
                game.year = year_value
                game.weight = weight
                game.weight_votes = weight_votes
                game.bgg_rank = bgg_rank
            elif bgg_rank is not None:
                game.bgg_rank = bgg_rank
            game.owned = bool(owners_for_gid)
            game.owned_by = owners_for_gid
            game.updated_at = now
            games_to_update.append(game)
        else:
            games_to_create.append(
                Game(
                    bgg_id=gid,
                    title=info.get('Game Title') or '',
                    type=game_type,
                    year=year_value,
                    avg_rating=avg_rating,
                    num_voters=num_voters,
                    weight=weight,
                    weight_votes=weight_votes,
                    bgg_rank=bgg_rank,
                    owned=bool(owners_for_gid),
                    owned_by=owners_for_gid,
                )
            )

    if games_to_create:
        Game.objects.bulk_create(games_to_create, batch_size=500)
    if games_to_update:
        Game.objects.bulk_update(
            games_to_update,
            ['title', 'type', 'year', 'avg_rating', 'num_voters', 'weight', 'weight_votes', 'bgg_rank', 'owned', 'owned_by', 'updated_at'],
            batch_size=500,
        )

    game_objs = Game.objects.in_bulk(normalized_ids, field_name='bgg_id')

    category_names = set()
    family_names = set()
    for gid in normalized_ids:
        detail = details_map.get(gid)
        if not detail:
            continue
        for name in detail.get('Categories') or []:
            if name:
                category_names.add(name)
        for name in detail.get('Families') or []:
            if name:
                family_names.add(name)

    category_map = _collect_vocab(Category, category_names)
    family_map = _collect_vocab(Family, family_names)

    for gid in normalized_ids:
        detail = details_map.get(gid)
        if not detail:
            continue
        game = game_objs.get(gid)
        if not game:
            continue
        cat_objs = [category_map[name] for name in (detail.get('Categories') or []) if name in category_map]
        fam_objs = [family_map[name] for name in (detail.get('Families') or []) if name in family_map]
        game.categories.set(cat_objs)
        game.families.set(fam_objs)

    detail_ids = [gid for gid in normalized_ids if gid in details_map]
    if detail_ids:
        existing_recs = PlayerCountRecommendation.objects.filter(
            game__bgg_id__in=detail_ids
        ).select_related('game')
        rec_map = {}
        for rec in existing_recs:
            rec_map.setdefault(rec.game.bgg_id, {})[rec.count] = rec

        recs_to_update = []
        recs_to_create = []
        recs_to_delete = []

        for gid in detail_ids:
            counts = player_counts_map.get(gid, {}) or {}
            game = game_objs.get(gid)
            if not game:
                continue
            seen_counts = set()
            for count, data in counts.items():
                try:
                    count_int = int(count)
                except (TypeError, ValueError):
                    continue
                best_pct = _to_float(data.get('Best %')) or 0.0
                best_votes = _to_int(data.get('Best Votes')) or 0
                rec_pct = _to_float(data.get('Rec. %')) or 0.0
                rec_votes = _to_int(data.get('Rec. Votes')) or 0
                notrec_pct = _to_float(data.get('Not %')) or 0.0
                notrec_votes = _to_int(data.get('Not Votes')) or 0
                vote_count = _to_int(data.get('Total Votes')) or 0
                seen_counts.add(count_int)
                existing = rec_map.get(gid, {}).get(count_int)
                if existing:
                    existing.best_pct = best_pct
                    existing.best_votes = best_votes
                    existing.rec_pct = rec_pct
                    existing.rec_votes = rec_votes
                    existing.notrec_pct = notrec_pct
                    existing.notrec_votes = notrec_votes
                    existing.vote_count = vote_count
                    recs_to_update.append(existing)
                else:
                    recs_to_create.append(
                        PlayerCountRecommendation(
                            game=game,
                            count=count_int,
                            best_pct=best_pct,
                            best_votes=best_votes,
                            rec_pct=rec_pct,
                            rec_votes=rec_votes,
                            notrec_pct=notrec_pct,
                            notrec_votes=notrec_votes,
                            vote_count=vote_count,
                        )
                    )
            existing_for_game = rec_map.get(gid, {})
            for count_val, rec_obj in existing_for_game.items():
                if count_val not in seen_counts:
                    recs_to_delete.append(rec_obj.id)

        if recs_to_delete:
            PlayerCountRecommendation.objects.filter(id__in=recs_to_delete).delete()
        if recs_to_update:
            PlayerCountRecommendation.objects.bulk_update(
                recs_to_update,
                ['best_pct', 'best_votes', 'rec_pct', 'rec_votes', 'notrec_pct', 'notrec_votes', 'vote_count'],
                batch_size=500,
            )
        if recs_to_create:
            PlayerCountRecommendation.objects.bulk_create(recs_to_create, batch_size=500)

    existing_owned = OwnedGame.objects.filter(game__bgg_id__in=normalized_ids).select_related('collection', 'game')
    existing_owned_by_gid = defaultdict(dict)
    for owned in existing_owned:
        if not owned.collection_id or not owned.game_id:
            continue
        existing_owned_by_gid[owned.game.bgg_id][owned.collection.username] = owned

    owned_to_create = []
    owned_to_delete = []

    for gid in normalized_ids:
        game = game_objs.get(gid)
        if not game:
            continue
        target_usernames = sorted(owners_lookup.get(gid, set()))
        existing_for_game = existing_owned_by_gid.get(gid, {})
        for username in target_usernames:
            coll = collection_cache.get(username)
            if coll is None:
                coll, _ = Collection.objects.get_or_create(username=username)
                collection_cache[username] = coll
            if username not in existing_for_game:
                owned_to_create.append(OwnedGame(collection=coll, game=game))
        for username, owned_obj in existing_for_game.items():
            if username not in target_usernames:
                owned_to_delete.append(owned_obj.id)

    if owned_to_create:
        OwnedGame.objects.bulk_create(owned_to_create, ignore_conflicts=True)
    if owned_to_delete:
        OwnedGame.objects.filter(id__in=owned_to_delete).delete()


def _prune_games_after_refresh(desired_ids):
    desired_ids = [str(gid) for gid in (desired_ids or [])]
    if desired_ids:
        Game.objects.exclude(bgg_id__in=desired_ids).delete()
    else:
        Game.objects.all().delete()

def run_fetch_top_n(job_id: int, n: int, ranks_zip_url: str | None = None):
    job = FetchJob.objects.get(id=job_id)
    job.status = "running"
    job.progress = 0
    job.total = n
    params = dict(job.params or {})
    if ranks_zip_url:
        params['zip_url'] = ranks_zip_url
    job.params = params
    job.save(update_fields=['status', 'progress', 'total', 'params'])
    log.info('Job %s: starting Top N fetch (n=%s)', job.id, n)

    ranks_zip_url = params.get('zip_url')

    client = BGGClient()
    try:
        if not ranks_zip_url:
            raise RuntimeError('A ranks ZIP URL must be provided to fetch ranked games.')
        log.info('Job %s: requesting Top N data from BGG', job.id)
        base_map = client.fetch_top_games_ranks(n, zip_url=ranks_zip_url)
        ids = list(base_map.keys())
        log.info('Job %s: received %s ranked games', job.id, len(ids))
        combined = {gid: dict(base_map[gid]) for gid in ids}
        job.total = len(ids)
        job.progress = len(ids)
        job.save(update_fields=["total", "progress"])

        log.info('Job %s: fetching details for %s games (batch=%s)', job.id, len(ids), 20)
        details, pcounts = client.fetch_details_batches(ids, batch_size=20)
        log.info('Job %s: details fetched for Top N job', job.id)

        _sync_catalog(combined, details, pcounts, prune=True)
        log.info('Job %s: catalog sync complete for Top N', job.id)

        job.status = "done"
        job.finished_at = timezone.now()
        job.progress = job.total
        job.save(update_fields=["status", "finished_at", "progress"])
        log.info('Job %s: Top N fetch finished successfully', job.id)
    except Exception as e:
        log.exception('Job %s: Top N job failed', job.id)
        job.status = "error"
        job.error = str(e) or 'Unknown error'
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])


def run_fetch_collection(job_id: int, username: str):
    job = FetchJob.objects.get(id=job_id)
    job.status = "running"
    job.progress = 0
    job.save(update_fields=["status", "progress"])
    log.info('Job %s: starting collection fetch for user %s', job.id, username)

    client = BGGClient()
    try:
        log.info('Job %s: requesting owned collection for %s', job.id, username)
        owned_map = client.fetch_owned_collection(username)
        ids = list(owned_map.keys())
        log.info('Job %s: collection returned %s items', job.id, len(ids))
        combined = {gid: dict(owned_map[gid]) for gid in ids}
        for gid in ids:
            combined[gid]["Owned"] = "Owned"
        job.total = len(ids)
        job.progress = len(ids)
        job.save(update_fields=["total", "progress"])

        log.info('Job %s: fetching details for %s collection games (batch=%s)', job.id, len(ids), 20)
        details, pcounts = client.fetch_details_batches(ids, batch_size=20)
        log.info('Job %s: details fetched for collection job', job.id)

        collection_map = {username: set(ids)}
        _sync_catalog(combined, details, pcounts, collection_owned_map=collection_map, prune=False)
        log.info('Job %s: catalog sync complete for collection job', job.id)

        job.status = "done"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
        log.info('Job %s: collection fetch finished successfully', job.id)
    except Exception as e:
        log.exception('Job %s: collection job failed', job.id)
        job.status = "error"
        job.error = str(e) or 'Unknown error'
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])

def start_background(target, *args, **kwargs):
    def _runner():
        close_old_connections()
        try:
            target(*args, **kwargs)
        finally:
            close_old_connections()

    th = threading.Thread(target=_runner, daemon=True)
    th.start()
    return th




def run_refresh(job_id: int, n: int, usernames: list[str] | None, batch_size: int, ranks_zip_url: str | None = None):
    job = FetchJob.objects.get(id=job_id)
    job.status = "running"
    job.progress = 0
    job.total = n

    normalized_usernames = sorted({(u or '').strip() for u in (usernames or []) if (u or '').strip()})
    _purge_untracked_collections(normalized_usernames)
    params = dict(job.params or {})
    params['batch_size'] = batch_size
    if ranks_zip_url:
        params['zip_url'] = ranks_zip_url
    params['usernames'] = normalized_usernames
    params["phases"] = {
        "top_n": {
            "status": "running",
            "progress": 0,
            "total": n,
            "started_at": timezone.now().isoformat(),
        },
        "collection": (
            {
                "status": "pending",
                "progress": 0,
                "total": len(normalized_usernames),
                "items": 0,
                "users_completed": 0,
            }
            if normalized_usernames
            else {"status": "skipped"}
        ),
        "details": {
            "status": "pending",
            "progress": 0,
            "total": 0,
            "batch": batch_size,
        },
        "cleanup": {"status": "pending", "progress": 0, "total": 1},
    }
    job.params = params
    job.save(update_fields=["status", "progress", "total", "params"])
    log.info('Job %s: starting refresh (n=%s, usernames=%s, batch=%s)', job.id, n, ','.join(normalized_usernames) or '-', batch_size)

    ranks_zip_url = params.get('zip_url')

    client = BGGClient()
    try:
        if not ranks_zip_url:
            raise RuntimeError('A ranks ZIP URL must be provided to fetch ranked games.')
        last_save = 0.0

        def save_throttled(fields):
            nonlocal last_save
            now_ts = time.time()
            if now_ts - last_save >= 0.5:
                job.save(update_fields=fields)
                last_save = now_ts

        def on_top_progress(**kw):
            params_local = job.params or {}
            ph = params_local.get("phases", {}).get("top_n", {})
            ph["progress"] = kw.get("progress", ph.get("progress", 0))
            ph["total"] = kw.get("total", ph.get("total", n))
            ph["updated_at"] = timezone.now().isoformat()
            params_local["phases"]["top_n"] = ph
            job.params = params_local
            job.progress = ph["progress"]
            save_throttled(["params", "progress"])

        log.info('Job %s: fetching Top N list (n=%s)', job.id, n)
        top_map = client.fetch_top_games_ranks(n, on_progress=on_top_progress, zip_url=ranks_zip_url)
        top_ids = list(top_map.keys())
        log.info('Job %s: Top N list returned %s games', job.id, len(top_ids))
        params_local = job.params or {}
        progress_value = len(top_ids) if n <= 0 else min(len(top_ids), n)
        params_local["phases"]["top_n"].update(
            {
                "status": "done",
                "progress": progress_value,
                "total": len(top_ids),
                "finished_at": timezone.now().isoformat(),
            }
        )
        job.params = params_local
        job.progress = progress_value
        job.save(update_fields=["params", "progress"])

        combined = {gid: dict(top_map[gid]) for gid in top_ids}
        collections_map: dict[str, set[str]] = {}
        total_collection_items = 0

        if normalized_usernames:
            params_local = job.params or {}
            coll_phase = params_local.get("phases", {}).get("collection")
            if coll_phase is not None:
                coll_phase.update(
                    {
                        "status": "running",
                        "progress": 0,
                        "total": len(normalized_usernames),
                        "items": 0,
                        "users_completed": 0,
                        "started_at": timezone.now().isoformat(),
                    }
                )
                params_local["phases"]["collection"] = coll_phase
                job.params = params_local
                job.save(update_fields=["params"])

            for idx, username in enumerate(normalized_usernames, 1):
                def on_coll_progress(**kw):
                    params_inner = job.params or {}
                    ph = params_inner.get("phases", {}).get("collection", {})
                    ph["status"] = "running"
                    ph["total"] = len(normalized_usernames)
                    ph["progress"] = idx - 1
                    ph["current_user"] = username
                    if "progress" in kw:
                        ph["user_progress"] = kw["progress"]
                    if "total" in kw:
                        ph["user_total"] = kw["total"]
                    if "items" in kw:
                        ph["current_items"] = kw["items"]
                    ph["updated_at"] = timezone.now().isoformat()
                    params_inner["phases"]["collection"] = ph
                    job.params = params_inner
                    save_throttled(["params"])

                log.info('Job %s: fetching owned collection for %s', job.id, username)
                user_owned_map = client.fetch_owned_collection(username, on_progress=on_coll_progress)
                user_ids = {str(gid) for gid in user_owned_map.keys()}
                collections_map[username] = user_ids
                total_collection_items += len(user_ids)
                log.info('Job %s: owned collection for %s returned %s items', job.id, username, len(user_ids))
                for gid in user_ids:
                    data = user_owned_map.get(gid, {}) or {}
                    entry = combined.setdefault(gid, dict(data))
                    entry["Owned"] = "Owned"
                    if data.get("Game Title"):
                        entry["Game Title"] = data["Game Title"]
                    if data.get("Type") is not None:
                        entry["Type"] = data["Type"]
                    if data.get("Average Rating") is not None:
                        entry["Average Rating"] = data["Average Rating"]
                    if data.get("Number of Voters") is not None:
                        entry["Number of Voters"] = data["Number of Voters"]

                params_local = job.params or {}
                ph = params_local.get("phases", {}).get("collection", {})
                ph.update(
                    {
                        "status": "running" if idx < len(normalized_usernames) else "done",
                        "progress": idx,
                        "users_completed": idx,
                        "total": len(normalized_usernames),
                        "items": total_collection_items,
                        "last_user": username,
                        "updated_at": timezone.now().isoformat(),
                    }
                )
                if idx == len(normalized_usernames):
                    ph["finished_at"] = timezone.now().isoformat()
                    ph.pop("current_user", None)
                    ph.pop("user_progress", None)
                    ph.pop("user_total", None)
                    ph.pop("current_items", None)
                params_local["phases"]["collection"] = ph
                job.params = params_local
                job.save(update_fields=["params"])
        else:
            collections_map = {}

        owners_lookup = _build_owners_lookup(collections_map)
        collection_cache = _ensure_collections(collections_map.keys())
        all_ids = [str(gid) for gid in combined.keys()]

        params_local = job.params or {}
        params_local["phases"]["details"].update(
            {
                "status": "running",
                "progress": 0,
                "total": len(all_ids),
                "batch": batch_size,
                "started_at": timezone.now().isoformat(),
            }
        )
        job.params = params_local
        job.total = len(all_ids)
        job.save(update_fields=["total", "params"])
        last_details_logged = -max(batch_size * 5, 100)
        last_details_status = None

        def on_details_progress(**kw):
            nonlocal last_details_logged, last_details_status
            params_inner = job.params or {}
            ph = params_inner.get("phases", {}).get("details", {})
            if "processed" in kw:
                ph["progress"] = kw["processed"]
            if "total" in kw:
                ph["total"] = kw["total"]
            if "batch" in kw:
                ph["batch"] = kw["batch"]
            status_val = kw.get("status")
            if status_val:
                ph["status"] = status_val
            ph["updated_at"] = timezone.now().isoformat()
            params_inner["phases"]["details"] = ph
            job.params = params_inner
            job.progress = ph.get("progress", job.progress)
            job.total = ph.get("total", job.total)

            processed = ph.get("progress") or 0
            total_local = ph.get("total") or 0
            batch_local = ph.get("batch") or batch_size
            if status_val and status_val != last_details_status:
                log.info('Job %s: details phase status changed to %s', job.id, status_val)
                last_details_status = status_val
            should_log = False
            if total_local:
                if processed == 0 or processed >= total_local:
                    should_log = True
                elif processed - last_details_logged >= max(batch_local * 5, 100):
                    should_log = True
            if should_log:
                log.info('Job %s: details processed %s/%s (batch=%s)', job.id, processed, total_local or '?', batch_local)
                last_details_logged = processed

            save_throttled(["params", "progress", "total"])

        log.info('Job %s: fetching details for %s games (batch=%s)', job.id, len(all_ids), batch_size)
        detail_stream = client.stream_details_batches(all_ids, batch_size=batch_size, on_progress=on_details_progress)

        for chunk_ids, details_chunk, pcounts_chunk in detail_stream:
            _sync_refresh_chunk(
                chunk_ids,
                combined,
                details_chunk,
                pcounts_chunk,
                owners_lookup,
                collection_cache,
            )

        log.info('Job %s: details phase completed', job.id)

        params_local = job.params or {}
        if "details" in params_local.get("phases", {}):
            params_local["phases"]["details"].update(
                {
                    "status": "done",
                    "progress": len(all_ids),
                    "total": len(all_ids),
                    "batch": batch_size,
                    "finished_at": timezone.now().isoformat(),
                }
            )
            job.params = params_local
            job.save(update_fields=["params"])

        params_local = job.params or {}
        if "cleanup" in params_local.get("phases", {}):
            params_local["phases"]["cleanup"].update(
                {
                    "status": "running",
                    "progress": 0,
                    "total": 1,
                    "started_at": timezone.now().isoformat(),
                }
            )
            params_local["current_phase"] = "cleanup"
            job.params = params_local
            job.save(update_fields=["params"])

        _prune_games_after_refresh(all_ids)
        log.info('Job %s: catalog cleanup complete for refresh', job.id)

        params_local = job.params or {}
        if "cleanup" in params_local.get("phases", {}):
            params_local["phases"]["cleanup"].update(
                {
                    "status": "done",
                    "progress": 1,
                    "total": 1,
                    "finished_at": timezone.now().isoformat(),
                }
            )
            job.params = params_local
            job.save(update_fields=["params"])

        job.status = "done"
        job.finished_at = timezone.now()
        job.progress = job.total
        job.save(update_fields=["status", "finished_at", "progress", "params"])
        params_local = job.params or {}
        params_local["current_phase"] = "done"
        job.params = params_local
        log.info('Job %s: refresh job finished successfully', job.id)

    except Exception as e:
        log.exception('Job %s: refresh job failed', job.id)
        job.status = "error"
        job.error = str(e) or 'Unknown error'
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
