from django.test import TestCase

from games.models import (
    Category,
    Collection,
    Family,
    Game,
    Mechanic,
    OwnedGame,
)
from games.tasks import (
    _apply_relations,
    _ensure_collections,
    _build_owners_lookup,
    _prune_games_after_refresh,
    _sync_catalog,
    _sync_refresh_chunk,
)
from games.utils import recompute_owned_flags
from .factories import game_detail, game_info, player_count


class SyncCatalogTests(TestCase):
    def test_creates_games_vocab_and_player_counts(self):
        games_map = {"13": game_info(13, "Catan")}
        details_map = {
            "13": game_detail(
                year=1995,
                categories=["Negotiation", "Economic"],
                families=["Strategy"],
                mechanics=["Dice Rolling", "Trading"],
            )
        }
        player_counts = {"13": {"3": player_count(best_votes=50, rec_votes=20),
                                "4": player_count(best_votes=80, rec_votes=10)}}

        _sync_catalog(games_map, details_map, player_counts, prune=True)

        game = Game.objects.get(bgg_id="13")
        self.assertEqual(game.title, "Catan")
        self.assertEqual(game.year, 1995)  # stored as an int now
        self.assertEqual(set(game.categories.values_list("name", flat=True)),
                         {"Negotiation", "Economic"})
        self.assertEqual(set(game.families.values_list("name", flat=True)), {"Strategy"})
        self.assertEqual(set(game.mechanics.values_list("name", flat=True)),
                         {"Dice Rolling", "Trading"})
        self.assertEqual(game.player_counts.count(), 2)
        # No duplicate vocab rows created.
        self.assertEqual(Category.objects.count(), 2)
        self.assertEqual(Family.objects.count(), 1)
        self.assertEqual(Mechanic.objects.count(), 2)

    def test_prune_removes_games_not_in_payload(self):
        Game.objects.create(bgg_id="999", title="Stale", type="Base Game")
        games_map = {"13": game_info(13)}
        details_map = {"13": game_detail()}

        _sync_catalog(games_map, details_map, {"13": {}}, prune=True)

        self.assertTrue(Game.objects.filter(bgg_id="13").exists())
        self.assertFalse(Game.objects.filter(bgg_id="999").exists())

    def test_rerun_updates_without_duplicating(self):
        games_map = {"13": game_info(13, "Catan", avg_rating=7.0)}
        details_map = {"13": game_detail(categories=["Economic"])}
        _sync_catalog(games_map, details_map, {"13": {"2": player_count(best_votes=10)}})

        # Re-run with changed data; player count 2 removed, 3 added.
        games_map = {"13": game_info(13, "Catan: New Edition", avg_rating=7.9)}
        details_map = {"13": game_detail(categories=["Negotiation"])}
        _sync_catalog(games_map, details_map, {"13": {"3": player_count(best_votes=5)}})

        self.assertEqual(Game.objects.count(), 1)
        game = Game.objects.get(bgg_id="13")
        self.assertEqual(game.title, "Catan: New Edition")
        self.assertEqual(game.avg_rating, 7.9)
        self.assertEqual(set(game.categories.values_list("name", flat=True)), {"Negotiation"})
        self.assertEqual(list(game.player_counts.values_list("count", flat=True)), [3])

    def test_collection_ownership_sets_denormalized_flags(self):
        games_map = {"13": game_info(13)}
        details_map = {"13": game_detail()}
        _sync_catalog(
            games_map,
            details_map,
            {"13": {}},
            collection_owned_map={"alice": {"13"}},
        )

        game = Game.objects.get(bgg_id="13")
        self.assertTrue(game.owned)
        self.assertEqual(game.owned_by, ["alice"])
        self.assertTrue(OwnedGame.objects.filter(game=game, collection__username="alice").exists())


class RefreshChunkTests(TestCase):
    def test_chunk_creates_and_owns(self):
        games_map = {"5": game_info(5, "Acquire")}
        details_map = {"5": game_detail(mechanics=["Stock Holding"])}
        player_counts = {"5": {"4": player_count(best_votes=30, rec_votes=10)}}
        owners_lookup = _build_owners_lookup({"bob": {"5"}})
        collection_cache = _ensure_collections(["bob"])

        _sync_refresh_chunk(["5"], games_map, details_map, player_counts,
                            owners_lookup, collection_cache)

        game = Game.objects.get(bgg_id="5")
        self.assertEqual(game.title, "Acquire")
        self.assertTrue(game.owned)
        self.assertEqual(game.owned_by, ["bob"])
        self.assertEqual(set(game.mechanics.values_list("name", flat=True)), {"Stock Holding"})
        self.assertEqual(game.player_counts.count(), 1)

    def test_chunk_removes_dropped_ownership(self):
        games_map = {"5": game_info(5)}
        details_map = {"5": game_detail()}
        owners_lookup = _build_owners_lookup({"bob": {"5"}})
        cache = _ensure_collections(["bob"])
        _sync_refresh_chunk(["5"], games_map, details_map, {"5": {}}, owners_lookup, cache)

        # Now bob no longer owns it.
        _sync_refresh_chunk(["5"], games_map, details_map, {"5": {}}, {}, cache)
        game = Game.objects.get(bgg_id="5")
        self.assertFalse(game.owned)
        self.assertEqual(game.owned_by, [])
        self.assertFalse(OwnedGame.objects.filter(game=game).exists())


class ApplyRelationsTests(TestCase):
    def test_apply_relations_idempotent(self):
        game = Game.objects.create(bgg_id="7", title="X", type="Base Game")
        game_objs = {"7": game}
        details = {"7": game_detail(categories=["A"], mechanics=["M1", "M2"])}
        _apply_relations(["7"], game_objs, details)
        _apply_relations(["7"], game_objs, details)
        self.assertEqual(set(game.categories.values_list("name", flat=True)), {"A"})
        self.assertEqual(set(game.mechanics.values_list("name", flat=True)), {"M1", "M2"})
        self.assertEqual(Mechanic.objects.count(), 2)


class PruneAfterRefreshTests(TestCase):
    def test_prune_with_ids(self):
        Game.objects.create(bgg_id="1", title="Keep", type="Base Game")
        Game.objects.create(bgg_id="2", title="Drop", type="Base Game")
        _prune_games_after_refresh(["1"])
        self.assertEqual(list(Game.objects.values_list("bgg_id", flat=True)), ["1"])

    def test_prune_empty_clears_all_without_error(self):
        # Regression: an orphaned ``job.save`` previously raised NameError here.
        Game.objects.create(bgg_id="1", title="A", type="Base Game")
        _prune_games_after_refresh([])
        self.assertEqual(Game.objects.count(), 0)


class RecomputeOwnedFlagsTests(TestCase):
    def test_recompute_reflects_ownership(self):
        game = Game.objects.create(bgg_id="1", title="A", type="Base Game",
                                   owned=True, owned_by=["ghost"])
        coll = Collection.objects.create(username="alice")
        # No OwnedGame row yet -> should clear flags.
        recompute_owned_flags([game.id])
        game.refresh_from_db()
        self.assertFalse(game.owned)
        self.assertEqual(game.owned_by, [])

        OwnedGame.objects.create(collection=coll, game=game)
        recompute_owned_flags([game.id])
        game.refresh_from_db()
        self.assertTrue(game.owned)
        self.assertEqual(game.owned_by, ["alice"])
