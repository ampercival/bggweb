from django.utils import timezone
from .models import Game, OwnedGame
from .queries import GameFilter  # noqa: F401
from .scoring import PLAYABLE_THRESHOLD, normalized_score
from .catalog import COLUMNS


def recompute_owned_flags(game_ids):
    """Recompute the denormalized ``Game.owned`` / ``Game.owned_by`` fields for
    the given Game primary keys based on the current ``OwnedGame`` rows.

    This is the single source of truth for keeping the denormalized ownership
    fields in sync with the ``OwnedGame`` through-table.
    """
    unique_ids = {gid for gid in (game_ids or []) if gid}
    if not unique_ids:
        return
    owned_rows = OwnedGame.objects.filter(game_id__in=unique_ids).select_related(
        "collection", "game"
    )
    owners_by_game = {}
    for owned in owned_rows:
        if not owned.game_id or not owned.collection_id or not owned.collection.username:
            continue
        owners_by_game.setdefault(owned.game_id, set()).add(owned.collection.username)
    updates = []
    now = timezone.now()
    for game in Game.objects.filter(id__in=unique_ids):
        owners = sorted(owners_by_game.get(game.id, []))
        if game.owned != bool(owners) or list(game.owned_by or []) != owners:
            game.owned = bool(owners)
            game.owned_by = owners
            game.updated_at = now
            updates.append(game)
    if updates:
        Game.objects.bulk_update(updates, ["owned", "owned_by", "updated_at"])

def serialize_game_row(rec, pc_range, pc_min):
    game = rec.game
    # Use ``.all()`` (not ``.values_list``) so the prefetched M2M cache is
    # reused instead of issuing a fresh query per row.
    categories = [c.name for c in game.categories.all()]
    families = [f.name for f in game.families.all()]
    mechanics = [m.name for m in game.mechanics.all()]
    owners_list = sorted(list(game.owned_by or []))
    pc_unadj = float(rec.pc_score_unadj or 0.0)
    
    pc_score_val = normalized_score(pc_unadj, pc_min, pc_range)

    score_factor_val = float(rec.score_factor or 0.0)
    
    row = {
        'title': game.title,
        'game_id': game.bgg_id,
        'year': game.year,
        'playtime_label': game.playtime_label,
        'min_playtime': game.min_playtime,
        'max_playtime': game.max_playtime,
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
        'mechanics': mechanics,
        'mechanics_str': ', '.join(sorted(mechanics)),
        'player_count': f'{rec.count}+' if rec.is_open_ended else rec.count,
        'people': [owner for owner in owners_list if owner not in ['Board Game Arena', 'Rally the Troops']],
        'platforms': [owner for owner in owners_list if owner in ['Board Game Arena', 'Rally the Troops']],
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
        'playable': ('No poll votes' if not rec.vote_count else
                     'Playable' if pc_unadj >= PLAYABLE_THRESHOLD else 'Not Playable'),
    }

    row['cells'] = [{'key': key, 'value': row.get(value_key)} for key, _, value_key in COLUMNS]
    return row
