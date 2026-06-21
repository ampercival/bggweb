from django.http import QueryDict
from django.test import TestCase

from games.models import Category, Family, Game, Mechanic, PlayerCountRecommendation
from games.utils import GameFilter, serialize_game_row


def _qd(**params):
    qd = QueryDict(mutable=True)
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for v in value:
                qd.appendlist(key, v)
        else:
            qd[key] = value
    return qd


class ScoringTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(
            bgg_id="13", title="Catan", type="Base Game", year=1995,
            avg_rating=8.0, num_voters=5000, weight=2.3, bgg_rank=1,
        )
        # pc_score_unadj = best%*3 + rec%*2 - not%*2
        # Use 100% best -> 300 (clearly "playable").
        PlayerCountRecommendation.objects.create(
            game=self.game, count=4, best_pct=100.0, best_votes=10,
            rec_pct=0.0, notrec_pct=0.0, vote_count=10,
        )

    def test_pc_score_unadj_and_playable(self):
        qs, _pre, pc_range, pc_min = GameFilter(_qd()).get_queryset()
        rec = qs.first()
        row = serialize_game_row(rec, pc_range, pc_min)
        self.assertEqual(row["pc_score_unadj"], 300.0)
        self.assertEqual(row["playable"], "Playable")

    def test_not_playable_below_threshold(self):
        g = Game.objects.create(bgg_id="99", title="Meh", type="Base Game",
                                avg_rating=6.0, num_voters=10)
        PlayerCountRecommendation.objects.create(
            game=g, count=2, best_pct=10.0, rec_pct=10.0, notrec_pct=80.0, vote_count=5,
        )
        qs, _pre, pc_range, pc_min = GameFilter(_qd(playable="all")).get_queryset()
        row = next(serialize_game_row(r, pc_range, pc_min)
                   for r in qs if r.game.bgg_id == "99")
        # 10*3 + 10*2 - 80*2 = 30 + 20 - 160 = -110 -> not playable
        self.assertEqual(row["pc_score_unadj"], -110.0)
        self.assertEqual(row["playable"], "Not Playable")

    def test_score_factor_formula(self):
        # With a single row, pc_score normalizes to 0 (range collapses), so
        # score_factor = (avg_rating*3 + 0) / 4.
        qs, _pre, pc_range, pc_min = GameFilter(_qd()).get_queryset()
        row = serialize_game_row(qs.first(), pc_range, pc_min)
        self.assertAlmostEqual(row["score_factor"], (8.0 * 3 + 0.0) / 4, places=3)

    def test_default_filters_exclude_not_playable(self):
        Game.objects.create(bgg_id="50", title="Bad", type="Base Game")
        bad = Game.objects.get(bgg_id="50")
        PlayerCountRecommendation.objects.create(
            game=bad, count=2, best_pct=0.0, rec_pct=0.0, notrec_pct=100.0, vote_count=3,
        )
        qs, _pre, _r, _m = GameFilter(_qd()).get_queryset()  # default playable=playable
        ids = {r.game.bgg_id for r in qs}
        self.assertIn("13", ids)
        self.assertNotIn("50", ids)


class FilterTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name="Economic")
        self.mech = Mechanic.objects.create(name="Trading")
        for gid, year, gtype in [("1", 1990, "Base Game"),
                                 ("2", 2020, "Base Game"),
                                 ("3", 2010, "Expansion")]:
            g = Game.objects.create(bgg_id=gid, title=f"G{gid}", type=gtype,
                                    year=year, avg_rating=7.0, num_voters=100)
            PlayerCountRecommendation.objects.create(
                game=g, count=4, best_pct=100.0, vote_count=10)
        Game.objects.get(bgg_id="1").categories.add(self.cat)
        Game.objects.get(bgg_id="1").mechanics.add(self.mech)

    def _ids(self, **params):
        qs, _pre, _r, _m = GameFilter(_qd(**params)).get_queryset()
        return {r.game.bgg_id for r in qs}

    def test_type_filter(self):
        self.assertEqual(self._ids(type="base"), {"1", "2"})
        self.assertEqual(self._ids(type="expansion"), {"3"})
        self.assertEqual(self._ids(type="all"), {"1", "2", "3"})

    def test_year_range_filter(self):
        self.assertEqual(self._ids(type="all", min_year="2005", max_year="2025"),
                         {"2", "3"})

    def test_category_filter(self):
        self.assertEqual(self._ids(type="all", categories="Economic"), {"1"})

    def test_mechanic_filter(self):
        self.assertEqual(self._ids(type="all", mechanics="Trading"), {"1"})

    def test_year_sort_uses_integer_order(self):
        qs, _pre, _r, _m = GameFilter(_qd(type="all", sort="year", dir="asc")).get_queryset()
        years = [r.game.year for r in qs]
        self.assertEqual(years, sorted(years))


class QueryCountTests(TestCase):
    def test_serialization_uses_prefetch_no_n_plus_one(self):
        for gid in range(1, 11):
            g = Game.objects.create(bgg_id=str(gid), title=f"G{gid}",
                                    type="Base Game", avg_rating=7.0, num_voters=10)
            g.categories.add(Category.objects.create(name=f"C{gid}"))
            g.mechanics.add(Mechanic.objects.create(name=f"M{gid}"))
            g.families.add(Family.objects.create(name=f"F{gid}"))
            PlayerCountRecommendation.objects.create(
                game=g, count=4, best_pct=100.0, vote_count=10)

        qs, _pre, pc_range, pc_min = GameFilter(_qd(page_size="50")).get_queryset()
        # Materialize with prefetch, then serialize. Serialization must not add
        # a query per row (regression guard for the .values_list N+1).
        recs = list(qs)
        with self.assertNumQueries(0):
            rows = [serialize_game_row(r, pc_range, pc_min) for r in recs]
        self.assertEqual(len(rows), 10)
