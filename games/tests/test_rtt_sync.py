from unittest import mock

from django.test import TestCase

from games.models import Collection, FetchJob, Game, OwnedGame, RTTGame
from games.tasks import (
    RTT_OWNER_LABEL,
    _purge_untracked_collections,
    run_scrape_rtt,
    sync_rtt_collection,
)


class SyncRTTCollectionTests(TestCase):
    def _game(self, bgg_id, title="G"):
        return Game.objects.create(bgg_id=bgg_id, title=title, type="Base Game")

    def test_tags_catalog_games_present_on_rtt(self):
        on = self._game("91", "Paths of Glory")
        off = self._game("13", "Catan")
        RTTGame.objects.create(bgg_id="91", slug="paths-of-glory", title="Paths of Glory")

        sync_rtt_collection()

        coll = Collection.objects.get(username=RTT_OWNER_LABEL)
        self.assertEqual(
            set(OwnedGame.objects.filter(collection=coll).values_list("game_id", flat=True)),
            {on.id},
        )
        on.refresh_from_db()
        off.refresh_from_db()
        self.assertTrue(on.owned)
        self.assertEqual(on.owned_by, [RTT_OWNER_LABEL])
        self.assertFalse(off.owned)
        self.assertEqual(off.owned_by, [])

    def test_removing_from_rtt_untags(self):
        game = self._game("91")
        RTTGame.objects.create(bgg_id="91", slug="paths-of-glory")
        sync_rtt_collection()

        RTTGame.objects.all().delete()
        sync_rtt_collection()

        coll = Collection.objects.get(username=RTT_OWNER_LABEL)
        self.assertFalse(OwnedGame.objects.filter(collection=coll).exists())
        game.refresh_from_db()
        self.assertFalse(game.owned)
        self.assertEqual(game.owned_by, [])

    def test_off_catalog_game_is_stored_and_tagged_later(self):
        # RTT knows about a game that is not yet in the catalog.
        RTTGame.objects.create(bgg_id="99999", slug="future-game")
        sync_rtt_collection()  # nothing to tag yet, must not error
        coll = Collection.objects.get(username=RTT_OWNER_LABEL)
        self.assertFalse(OwnedGame.objects.filter(collection=coll).exists())

        # A later refresh pulls the game into the catalog; re-syncing tags it.
        game = self._game("99999", "Future Game")
        sync_rtt_collection()

        self.assertTrue(OwnedGame.objects.filter(collection=coll, game=game).exists())
        game.refresh_from_db()
        self.assertTrue(game.owned)
        self.assertEqual(game.owned_by, [RTT_OWNER_LABEL])

    def test_refresh_purge_preserves_rtt_collection(self):
        self._game("91")
        RTTGame.objects.create(bgg_id="91", slug="paths-of-glory")
        sync_rtt_collection()
        # A user collection that should be purged, plus the RTT pseudo-collection.
        Collection.objects.create(username="staleuser")

        _purge_untracked_collections(["trackeduser"])

        self.assertTrue(Collection.objects.filter(username=RTT_OWNER_LABEL).exists())
        self.assertFalse(Collection.objects.filter(username="staleuser").exists())

    def test_reconcile_restores_ownership_stripped_mid_refresh(self):
        # Simulates the refresh chunk deleting the RTT OwnedGame row (because the
        # chunk's owners_lookup does not include RTT); the end-of-refresh
        # sync_rtt_collection() must put it back.
        game = self._game("91")
        RTTGame.objects.create(bgg_id="91", slug="paths-of-glory")
        sync_rtt_collection()
        coll = Collection.objects.get(username=RTT_OWNER_LABEL)
        OwnedGame.objects.filter(collection=coll).delete()  # what the chunk would do

        sync_rtt_collection()

        self.assertTrue(OwnedGame.objects.filter(collection=coll, game=game).exists())
        game.refresh_from_db()
        self.assertEqual(game.owned_by, [RTT_OWNER_LABEL])


class RunScrapeRTTJobTests(TestCase):
    def test_job_upserts_prunes_and_tags(self):
        # A catalog game that is on RTT, and a stale RTTGame that is no longer listed.
        game = Game.objects.create(bgg_id="91", title="Paths of Glory", type="Base Game")
        RTTGame.objects.create(bgg_id="404", slug="gone", title="Removed From RTT")
        job = FetchJob.objects.create(kind="rtt", params={}, status="pending", total=0)

        scraped = [
            {"bgg_id": "91", "slug": "paths-of-glory", "title": "Paths of Glory"},
            {"bgg_id": "888", "slug": "future-game", "title": "Not In Catalog Yet"},
        ]
        with mock.patch("games.tasks.RTTClient") as MockClient:
            MockClient.return_value.fetch_games.return_value = scraped
            run_scrape_rtt.now(job.id)

        # RTTGame table reflects exactly the scraped set (stale row pruned).
        self.assertEqual(set(RTTGame.objects.values_list("bgg_id", flat=True)), {"91", "888"})

        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(job.total, 2)
        self.assertEqual(job.progress, 2)

        # The catalog game is tagged; the off-catalog one is stored but untagged.
        game.refresh_from_db()
        self.assertEqual(game.owned_by, [RTT_OWNER_LABEL])
        coll = Collection.objects.get(username=RTT_OWNER_LABEL)
        self.assertEqual(
            set(OwnedGame.objects.filter(collection=coll).values_list("game__bgg_id", flat=True)),
            {"91"},
        )
