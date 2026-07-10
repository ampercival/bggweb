from unittest import mock

from django.test import SimpleTestCase

from games.services.rtt_client import RTTClient


class FakeResp:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self):
        return None


def _client():
    # No real network and no throttling delays in tests.
    c = RTTClient(throttle_sec=0)
    c._sleep = lambda *a, **k: None
    return c


LIBRARY_HTML = """
<html><body>
  <div class="game_list">
    <a href="/paths-of-glory"><img src="/paths-of-glory/thumbnail.jpg"> Paths of Glory</a>
    <a href="/julius-caesar"><img src="/julius-caesar/thumbnail.jpg"> Julius Caesar</a>
    <a href="/about">About</a>                       <!-- no thumbnail: skipped -->
    <a href="/games/library"><img src="/nav/icon.png"> Library</a>  <!-- two segments: skipped -->
    <a href="/paths-of-glory"><img src="/paths-of-glory/thumbnail.jpg"> Paths of Glory</a> <!-- dupe -->
  </div>
</body></html>
"""

GAME_HTML_WITH_BGG = """
<html><body>
  <h1>Paths of Glory</h1>
  <p class="notes">Read more about the game on
    <a href="https://boardgamegeek.com/boardgame/91">boardgamegeek.com</a>.</p>
</body></html>
"""

GAME_HTML_NO_BGG = "<html><body><h1>Mystery</h1><p>No links here.</p></body></html>"


class LibraryParsingTests(SimpleTestCase):
    def test_parses_game_tiles_and_skips_non_games(self):
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(text=LIBRARY_HTML)):
            games = client.fetch_library_slugs()

        self.assertEqual(
            games,
            [("paths-of-glory", "Paths of Glory"), ("julius-caesar", "Julius Caesar")],
        )

    def test_empty_library_raises(self):
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(text="<html></html>")):
            with self.assertRaises(RuntimeError):
                client.fetch_library_slugs()


class GamePageParsingTests(SimpleTestCase):
    def test_extracts_bgg_id(self):
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(text=GAME_HTML_WITH_BGG)):
            self.assertEqual(client.fetch_game_bgg_id("paths-of-glory"), "91")

    def test_returns_none_when_absent(self):
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(text=GAME_HTML_NO_BGG)):
            self.assertIsNone(client.fetch_game_bgg_id("mystery"))


class FetchGamesTests(SimpleTestCase):
    def test_scrapes_library_then_pages_and_skips_missing_ids(self):
        client = _client()

        def fake_get(url, **kwargs):
            if url.endswith("/games/library"):
                return FakeResp(text=LIBRARY_HTML)
            if url.endswith("/paths-of-glory"):
                return FakeResp(text=GAME_HTML_WITH_BGG)
            if url.endswith("/julius-caesar"):
                return FakeResp(text=GAME_HTML_NO_BGG)  # no BGG id -> skipped
            raise AssertionError(f"unexpected url {url}")

        seen = []
        with mock.patch.object(client, "_get", side_effect=fake_get):
            games = client.fetch_games(on_progress=lambda **kw: seen.append(kw))

        self.assertEqual(games, [{"bgg_id": "91", "slug": "paths-of-glory", "title": "Paths of Glory"}])
        # Progress reported the total (2 tiles) even though one was skipped.
        self.assertEqual(seen[-1], {"progress": 2, "total": 2})
