from unittest import mock

from django.test import TestCase

from games.models import BGAGame, Collection, FetchJob, Game, OwnedGame, RTTGame
from games.tasks import (
    BGA_OWNER_LABEL,
    RTT_OWNER_LABEL,
    _purge_untracked_collections,
    run_fetch_bga,
    sync_bga_collection,
    sync_platform_collections,
)


class SyncBGACollectionTests(TestCase):
    def _game(self, bgg_id, title="G"):
        return Game.objects.create(bgg_id=bgg_id, title=title, type="Base Game")

    def test_tags_catalog_games_in_family(self):
        on = self._game("13", "Catan")
        off = self._game("822", "Carcassonne")
        BGAGame.objects.create(bgg_id="13", title="Catan")

        sync_bga_collection()

        coll = Collection.objects.get(username=BGA_OWNER_LABEL)
        self.assertEqual(
            set(OwnedGame.objects.filter(collection=coll).values_list("game_id", flat=True)),
            {on.id},
        )
        on.refresh_from_db()
        off.refresh_from_db()
        self.assertEqual(on.owned_by, [BGA_OWNER_LABEL])
        self.assertFalse(off.owned)

    def test_off_catalog_game_tagged_later(self):
        BGAGame.objects.create(bgg_id="99999", title="Future")
        sync_bga_collection()  # nothing in catalog yet, must not error
        coll = Collection.objects.get(username=BGA_OWNER_LABEL)
        self.assertFalse(OwnedGame.objects.filter(collection=coll).exists())

        game = self._game("99999", "Future")
        sync_bga_collection()
        self.assertTrue(OwnedGame.objects.filter(collection=coll, game=game).exists())

    def test_game_on_both_platforms_lists_both_owners(self):
        game = self._game("13", "Catan")
        RTTGame.objects.create(bgg_id="13", slug="catan")
        BGAGame.objects.create(bgg_id="13", title="Catan")

        sync_platform_collections()

        game.refresh_from_db()
        self.assertEqual(game.owned_by, sorted([BGA_OWNER_LABEL, RTT_OWNER_LABEL]))

    def test_purge_preserves_bga_collection(self):
        self._game("13")
        BGAGame.objects.create(bgg_id="13")
        sync_bga_collection()

        _purge_untracked_collections(["someuser"])

        self.assertTrue(Collection.objects.filter(username=BGA_OWNER_LABEL).exists())


class RunFetchBGAJobTests(TestCase):
    def test_job_upserts_prunes_and_tags(self):
        game = Game.objects.create(bgg_id="13", title="Catan", type="Base Game")
        BGAGame.objects.create(bgg_id="404", title="Gone From BGA")
        job = FetchJob.objects.create(kind="bga", params={}, status="pending", total=0)

        members = [
            {"bgg_id": "13", "title": "Catan"},
            {"bgg_id": "888", "title": "Not In Catalog Yet"},
        ]
        with mock.patch("games.tasks.BGGClient") as MockClient:
            MockClient.return_value.fetch_family_members.return_value = members
            run_fetch_bga.now(job.id)

        self.assertEqual(set(BGAGame.objects.values_list("bgg_id", flat=True)), {"13", "888"})

        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(job.total, 2)
        self.assertEqual(job.params.get("family_id"), "70360")

        game.refresh_from_db()
        self.assertEqual(game.owned_by, [BGA_OWNER_LABEL])
        coll = Collection.objects.get(username=BGA_OWNER_LABEL)
        self.assertEqual(
            set(OwnedGame.objects.filter(collection=coll).values_list("game__bgg_id", flat=True)),
            {"13"},
        )
