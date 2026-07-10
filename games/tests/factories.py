"""Small helpers for building BGG-shaped fetch payloads used across tests."""


def game_info(gid, title=None, type="Base Game", avg_rating=7.5, num_voters=1000):
    """An entry as produced by the Top N / collection fetch (``games_map``)."""
    return {
        "Game Title": title or f"Game {gid}",
        "Game ID": str(gid),
        "Type": type,
        "Average Rating": avg_rating,
        "Number of Voters": num_voters,
        "Weight": None,
        "Weight Votes": None,
        "BGG Rank": int(gid) if str(gid).isdigit() else None,
        "Owned": "Not Owned",
    }


def game_detail(year=2015, weight=2.5, weight_votes=300, bgg_rank=1,
                categories=None, families=None, mechanics=None):
    """An entry as produced by the ``thing`` detail fetch (``details_map``)."""
    return {
        "Year": year,
        "Weight": weight,
        "Weight Votes": weight_votes,
        "BGG Rank": bgg_rank,
        "Categories": list(categories or []),
        "Families": list(families or []),
        "Mechanics": list(mechanics or []),
    }


def player_count(best_votes=0, rec_votes=0, notrec_votes=0):
    """A single player-count poll row with derived percentages."""
    total = best_votes + rec_votes + notrec_votes

    def pct(v):
        return round((v / total) * 100, 1) if total else 0.0

    return {
        "Best %": pct(best_votes),
        "Best Votes": best_votes,
        "Rec. %": pct(rec_votes),
        "Rec. Votes": rec_votes,
        "Not %": pct(notrec_votes),
        "Not Votes": notrec_votes,
        "Total Votes": total,
    }
