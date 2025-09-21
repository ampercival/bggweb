import threading
import time
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
    owned_ids=None,
    username=None,
    prune=False,
    progress_callback=None,
    progress_total=None,
):
    owned_ids = set(owned_ids or [])
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
        owned_units = len(owned_ids)
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
        owned = gid in owned_ids

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
            game.owned = owned
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
                    owned=owned,
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
                "owned",
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

    recs_to_create = []
    recs_to_update = []
    recs_to_delete = []
    for gid in desired_ids:
        game = game_objs.get(gid)
        if not game:
            continue
        counts_payload = player_counts.get(gid, {})
        seen_counts = set()
        for count_key, payload in counts_payload.items():
            try:
                count = int(count_key)
            except (TypeError, ValueError):
                continue
            seen_counts.add(count)
            payload = payload or {}
            best_pct = float(payload.get("Best %", 0.0) or 0.0)
            best_votes = _to_int(payload.get("Best Votes")) or 0
            rec_pct = float(payload.get("Recommended %", 0.0) or 0.0)
            rec_votes = _to_int(payload.get("Recommended Votes")) or 0
            notrec_pct = float(payload.get("Not Recommended %", 0.0) or 0.0)
            notrec_votes = _to_int(payload.get("Not Recommended Votes")) or 0
            vote_count = _to_int(payload.get("Vote Count")) or 0
            existing_for_game = rec_map.get(gid, {})
            rec_obj = existing_for_game.get(count)
            if rec_obj:
                rec_obj.best_pct = best_pct
                rec_obj.best_votes = best_votes
                rec_obj.rec_pct = rec_pct
                rec_obj.rec_votes = rec_votes
                rec_obj.notrec_pct = notrec_pct
                rec_obj.notrec_votes = notrec_votes
                rec_obj.vote_count = vote_count
                recs_to_update.append(rec_obj)
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

    if username:
        coll, _ = Collection.objects.get_or_create(username=username)
        target_owned_ids = {gid for gid in desired_ids if gid in owned_ids}
        existing_owned = OwnedGame.objects.filter(collection=coll).select_related("game")
        existing_owned_map = {og.game.bgg_id: og for og in existing_owned if og.game_id}
        owned_to_create = []
        for gid in target_owned_ids:
            if gid not in existing_owned_map:
                game = game_objs.get(gid)
                if game:
                    owned_to_create.append(OwnedGame(collection=coll, game=game))
        if owned_to_create:
            OwnedGame.objects.bulk_create(owned_to_create, ignore_conflicts=True)
        to_remove = [og.id for gid, og in existing_owned_map.items() if gid not in target_owned_ids]
        if to_remove:
            OwnedGame.objects.filter(id__in=to_remove).delete()
        report_units(owned_units)

    if track_progress and progress_target > 0 and last_progress < progress_target:
        progress_callback(progress_target)


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

    ranks_zip_url = params.get('zip_url')

    client = BGGClient()
    try:
        if not ranks_zip_url:
            raise RuntimeError('A ranks ZIP URL must be provided to fetch ranked games.')
        base_map = client.fetch_top_games_ranks(n, zip_url=ranks_zip_url)
        ids = list(base_map.keys())
        combined = {gid: dict(base_map[gid]) for gid in ids}
        job.total = len(ids)
        job.progress = len(ids)
        job.save(update_fields=["total", "progress"])

        details, pcounts = client.fetch_details_batches(ids, batch_size=20)

        _sync_catalog(combined, details, pcounts, owned_ids=set(), username=None, prune=True)

        job.status = "done"
        job.finished_at = timezone.now()
        job.progress = job.total
        job.save(update_fields=["status", "finished_at", "progress"])
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])


def run_fetch_collection(job_id: int, username: str):
    job = FetchJob.objects.get(id=job_id)
    job.status = "running"
    job.progress = 0
    job.save(update_fields=["status", "progress"])

    client = BGGClient()
    try:
        owned_map = client.fetch_owned_collection(username)
        ids = list(owned_map.keys())
        combined = {gid: dict(owned_map[gid]) for gid in ids}
        job.total = len(ids)
        job.progress = len(ids)
        job.save(update_fields=["total", "progress"])

        details, pcounts = client.fetch_details_batches(ids, batch_size=20)

        _sync_catalog(combined, details, pcounts, owned_ids=set(ids), username=username, prune=False)

        job.status = "done"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
    except Exception as e:
        job.status = "error"
        job.error = str(e)
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


def run_refresh(job_id: int, n: int, username: str | None, batch_size: int, ranks_zip_url: str | None = None):
    job = FetchJob.objects.get(id=job_id)
    job.status = "running"
    job.progress = 0
    job.total = n
    params = dict(job.params or {})
    params['batch_size'] = batch_size
    if ranks_zip_url:
        params['zip_url'] = ranks_zip_url
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
                "total": 0,
                "items": 0,
            }
            if username
            else {"status": "skipped"}
        ),
        "details": {
            "status": "pending",
            "progress": 0,
            "total": 0,
            "batch": batch_size,
        },
        "apply": {"status": "pending", "progress": 0, "total": 0},
    }
    job.params = params
    job.save(update_fields=["status", "progress", "total", "params"])

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

        top_map = client.fetch_top_games_ranks(n, on_progress=on_top_progress, zip_url=ranks_zip_url)
        top_ids = list(top_map.keys())
        params_local = job.params or {}
        params_local["phases"]["top_n"].update(
            {
                "status": "done",
                "progress": min(len(top_ids), n),
                "finished_at": timezone.now().isoformat(),
            }
        )
        job.params = params_local
        job.progress = min(len(top_ids), n)
        job.save(update_fields=["params", "progress"])

        owned_map = {}
        owned_ids = set()
        if username:
            params_local = job.params or {}
            coll_phase = params_local.get("phases", {}).get("collection")
            if coll_phase is not None:
                coll_phase.update(
                    {
                        "status": "running",
                        "progress": 0,
                        "items": 0,
                        "started_at": timezone.now().isoformat(),
                    }
                )
                params_local["phases"]["collection"] = coll_phase
                job.params = params_local
                job.save(update_fields=["params"])

            def on_coll_progress(**kw):
                params_inner = job.params or {}
                ph = params_inner.get("phases", {}).get("collection", {})
                if "progress" in kw:
                    ph["progress"] = kw["progress"]
                if "total" in kw:
                    ph["total"] = kw["total"]
                if "items" in kw:
                    ph["items"] = kw["items"]
                ph["updated_at"] = timezone.now().isoformat()
                params_inner["phases"]["collection"] = ph
                job.params = params_inner
                save_throttled(["params"])

            owned_map = client.fetch_owned_collection(username, on_progress=on_coll_progress)
            owned_ids = set(owned_map.keys())
            params_local = job.params or {}
            if "collection" in params_local.get("phases", {}):
                params_local["phases"]["collection"].update(
                    {
                        "status": "done",
                        "items": len(owned_map),
                        "finished_at": timezone.now().isoformat(),
                    }
                )
                job.params = params_local
                job.save(update_fields=["params"])

        combined = {gid: dict(top_map[gid]) for gid in top_ids}
        for gid in owned_ids:
            data = owned_map.get(gid, {})
            if gid in combined:
                entry = combined[gid]
                entry["Owned"] = "Owned"
                if data.get("Game Title"):
                    entry["Game Title"] = data["Game Title"]
                if data.get("Type") is not None:
                    entry["Type"] = data["Type"]
                if data.get("Average Rating") is not None:
                    entry["Average Rating"] = data["Average Rating"]
                if data.get("Number of Voters") is not None:
                    entry["Number of Voters"] = data["Number of Voters"]
            else:
                entry = dict(data)
                entry["Owned"] = "Owned"
                combined[gid] = entry

        all_ids = list(combined.keys())
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
        if "apply" in params_local.get("phases", {}):
            params_local["phases"]["apply"].update({"total": len(all_ids)})
        job.params = params_local
        job.total = len(all_ids)
        job.save(update_fields=["total", "params"])

        def on_details_progress(**kw):
            params_inner = job.params or {}
            ph = params_inner.get("phases", {}).get("details", {})
            if "processed" in kw:
                ph["progress"] = kw["processed"]
            if "total" in kw:
                ph["total"] = kw["total"]
            if "batch" in kw:
                ph["batch"] = kw["batch"]
            ph["updated_at"] = timezone.now().isoformat()
            params_inner["phases"]["details"] = ph
            job.params = params_inner
            job.progress = ph.get("progress", job.progress)
            job.total = ph.get("total", job.total)
            save_throttled(["params", "progress", "total"])

        details, pcounts = client.fetch_details_batches(all_ids, batch_size=batch_size, on_progress=on_details_progress)

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
        if "apply" in params_local.get("phases", {}):
            params_local["phases"]["apply"].update(
                {
                    "status": "running",
                    "progress": 0,
                    "total": len(all_ids),
                    "started_at": timezone.now().isoformat(),
                }
            )
            job.params = params_local
            job.save(update_fields=["params"])

        apply_total = len(all_ids)

        def on_apply_progress(value: int):
            if apply_total <= 0:
                return
            params_inner = job.params or {}
            ph = params_inner.get("phases", {}).get("apply", {})
            new_progress = max(ph.get("progress", 0), min(apply_total, int(value)))
            if new_progress == ph.get("progress"):
                return
            ph["progress"] = new_progress
            ph["updated_at"] = timezone.now().isoformat()
            params_inner["phases"]["apply"] = ph
            job.params = params_inner
            job.save(update_fields=["params"])

        progress_cb = on_apply_progress if apply_total > 0 else None
        progress_total = apply_total if apply_total > 0 else None
        _sync_catalog(
            combined,
            details,
            pcounts,
            owned_ids=owned_ids,
            username=username,
            prune=True,
            progress_callback=progress_cb,
            progress_total=progress_total,
        )

        params_local = job.params or {}
        if "apply" in params_local.get("phases", {}):
            params_local["phases"]["apply"].update(
                {
                    "status": "done",
                    "progress": len(all_ids),
                    "total": len(all_ids),
                    "finished_at": timezone.now().isoformat(),
                }
            )
            job.params = params_local

        job.status = "done"
        job.finished_at = timezone.now()
        job.progress = job.total
        job.save(update_fields=["status", "finished_at", "progress", "params"])
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
