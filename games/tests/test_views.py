import csv
import io
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from games.models import FetchJob, Game, PlayerCountRecommendation
from games.views import CSV_HEADER


def _make_game(gid, gtype="Base Game", best_pct=100.0):
    g = Game.objects.create(bgg_id=gid, title=f"G{gid}", type=gtype,
                            year=2010, avg_rating=7.5, num_voters=100)
    PlayerCountRecommendation.objects.create(
        game=g, count=4, best_pct=best_pct, vote_count=10)
    return g


# Tests run with DEBUG=False, which enables SECURE_SSL_REDIRECT (301 to https);
# disable it so the test client can exercise the views over plain HTTP.
@override_settings(SECURE_SSL_REDIRECT=False)
class ClearJobsAuthTests(TestCase):
    def setUp(self):
        self.job = FetchJob.objects.create(kind="refresh", status="done")

    def test_anonymous_cannot_clear_jobs(self):
        resp = self.client.post(reverse("clear_jobs"))
        self.assertEqual(resp.status_code, 302)  # redirected to login
        self.assertTrue(FetchJob.objects.filter(id=self.job.id).exists())

    def test_superuser_can_clear_jobs(self):
        User = get_user_model()
        User.objects.create_superuser("admin", "a@example.com", "pw")
        self.client.login(username="admin", password="pw")
        resp = self.client.post(reverse("clear_jobs"))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(FetchJob.objects.exists())


@override_settings(SECURE_SSL_REDIRECT=False)
class ExportCsvTests(TestCase):
    def setUp(self):
        _make_game("1")
        _make_game("2")
        _make_game("3", gtype="Expansion")

    def _read_csv(self, **params):
        resp = self.client.get(reverse("export_csv"), params)
        content = b"".join(resp.streaming_content).decode("utf-8")
        return list(csv.reader(io.StringIO(content)))

    def test_export_includes_all_rows_ignoring_pagination(self):
        # page_size=1 must NOT limit the export (regression for the
        # "export only current page" bug).
        rows = self._read_csv(type="all", playable="all", page_size="1")
        self.assertEqual(rows[0], CSV_HEADER)
        self.assertEqual(len(rows) - 1, 3)  # 3 data rows

    def test_header_and_rows_have_25_columns(self):
        rows = self._read_csv(type="all", playable="all")
        self.assertEqual(len(rows[0]), 25)
        for data_row in rows[1:]:
            self.assertEqual(len(data_row), 25)

    def test_export_respects_filters(self):
        rows = self._read_csv(type="expansion", playable="all")
        data = rows[1:]
        self.assertEqual(len(data), 1)
        # Game ID column is index 2.
        self.assertEqual(data[0][2], "3")


@override_settings(SECURE_SSL_REDIRECT=False)
class GamesTableColumnTests(TestCase):
    def setUp(self):
        _make_game("1")

    def _count(self, html, tag):
        return len(re.findall(r"<%s\b" % tag, html))

    def test_header_and_body_column_counts_match(self):
        page = self.client.get(reverse("games_list")).content.decode("utf-8")
        # The catalog page has a single data-table; count its header cells.
        header_count = self._count(page, "th")

        rows_resp = self.client.get(
            reverse("games_rows"),
            {"type": "all", "playable": "all"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        row_html = rows_resp.json()["html"]
        first_row = row_html.split("</tr>")[0]
        body_count = self._count(first_row, "td")

        self.assertEqual(header_count, 25)
        self.assertEqual(body_count, 25)


@override_settings(SECURE_SSL_REDIRECT=False)
class GameDetailTests(TestCase):
    def test_game_detail_renders(self):
        _make_game("42")
        resp = self.client.get(reverse("game_detail", args=["42"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "G42")

    def test_game_detail_404(self):
        resp = self.client.get(reverse("game_detail", args=["nope"]))
        self.assertEqual(resp.status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False)
class GamesRowsPaginationTests(TestCase):
    def test_rows_endpoint_reports_counts(self):
        for gid in range(1, 6):
            _make_game(str(gid))
        resp = self.client.get(
            reverse("games_rows"),
            {"type": "all", "playable": "all", "page_size": "2"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = resp.json()
        self.assertEqual(data["count"], 5)
        self.assertEqual(data["page_size"], 2)
        self.assertEqual(data["num_pages"], 3)
