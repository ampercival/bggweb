"""Optional real-browser acceptance: python manage.py test browser_tests."""
import csv
import io
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError as exc:
    raise unittest.SkipTest('Install requirements-browser.txt to run browser acceptance.') from exc

from games.models import FetchJob
from games.catalog import COLUMNS
from games.tasks import _sync_catalog
from games.tests.factories import game_info, game_detail, player_count
from games.tests.test_views import VIEW_TEST_SETTINGS


@override_settings(**VIEW_TEST_SETTINGS)
class CatalogBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        _sync_catalog(
            {'1': game_info(1, 'Dice game'), '2': game_info(2, 'Trading game')},
            {'1': game_detail(mechanics=['Dice Rolling'], playing_time=30, min_playtime=20, max_playtime=30),
             '2': game_detail(mechanics=['Trading'], playing_time=120, min_playtime=60, max_playtime=120)},
            {'1': {'4': player_count(best_votes=50)}, '2': {'4': player_count(best_votes=50)}},
        )
        self.job = FetchJob.objects.create(kind='top_n')
        # Start Playwright only after synchronous ORM fixture setup, and stop
        # its event loop before Django performs synchronous database teardown.
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()
        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 900})
        self.page = self.context.new_page()

    def tearDown(self):
        directory = Path('artifacts/browser')
        directory.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(directory / (self._testMethodName + '.png')), full_page=True)
        self.context.close()
        self.browser.close()
        self.playwright.stop()

    def test_multiple_filters_reload_and_csv_agree(self):
        self.page.goto(self.live_server_url + '/games/')
        self.page.locator('#advancedFilters summary').first.click()
        self.page.locator('[name="mechanics"][value="Dice Rolling"]').check()
        expect(self.page.locator('#resultsNotice')).to_contain_text('1 matching')
        self.page.locator('[name="mechanics"][value="Trading"]').check()
        expect(self.page.locator('#resultsNotice')).to_contain_text('2 matching')
        params = parse_qs(urlsplit(self.page.url).query)
        self.assertEqual(params['mechanics'], ['Dice Rolling', 'Trading'])
        export_url = self.page.locator('#filteredExport').get_attribute('href')
        exported = self.context.request.get(self.live_server_url + export_url)
        rows = list(csv.reader(io.StringIO(exported.text())))
        self.assertEqual(len(rows), 3)
        self.assertIn('Families', rows[0])
        self.page.reload()
        expect(self.page.locator('[name="mechanics"]:checked')).to_have_count(2)

    def test_phone_layout_and_detailed_columns(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.goto(self.live_server_url + '/games/')
        self.assertLess(self.page.locator('.results-card').bounding_box()['y'], 900)
        expect(self.page.locator('#advancedFilters')).not_to_have_attribute('open', '')
        expect(self.page.locator('th:visible')).to_have_count(8)
        directory = Path('artifacts/browser')
        directory.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(directory / 'mobile-catalog.png'))
        self.page.locator('#detailedColumns').check()
        expect(self.page.locator('th:visible')).to_have_count(len(COLUMNS))

    def test_playtime_filter_reload_sort_and_export(self):
        self.page.goto(self.live_server_url + '/games/')
        self.page.locator('#max_playtime').fill('60')
        expect(self.page.locator('#resultsNotice')).to_contain_text('1 matching')
        expect(self.page.locator('#rows')).to_contain_text('Dice game')
        expect(self.page.locator('#rows')).not_to_contain_text('Trading game')
        expect(self.page.locator('#activeFilters')).to_contain_text('At most 60 min')
        self.page.locator('th a[data-sort="playtime"]').click()
        expect(self.page.locator('th[data-column="playtime"]')).to_have_attribute('aria-sort', 'desc')
        export_url = self.page.locator('#filteredExport').get_attribute('href')
        rows = list(csv.DictReader(io.StringIO(self.context.request.get(self.live_server_url + export_url).text())))
        self.assertEqual([row['Game'] for row in rows], ['Dice game'])
        self.assertEqual(rows[0]['Play time (min)'], '20\u201330')
        self.page.reload()
        expect(self.page.locator('#max_playtime')).to_have_value('60')
        expect(self.page.locator('#rows')).not_to_contain_text('Trading game')
        self.page.locator('#max_playtime').fill('')
        expect(self.page.locator('#resultsNotice')).to_contain_text('2 matching')

    def test_job_polling_recovers_and_renders_new_error(self):
        job = self.job
        self.page.goto(self.live_server_url + f'/jobs/{job.pk}/')
        expect(self.page.locator('#cancelJobForm')).to_have_count(0)
        attempts = []

        def poll(route):
            attempts.append(True)
            if len(attempts) == 1:
                route.fulfill(status=503, body='temporarily unavailable')
            else:
                route.fulfill(content_type='application/json', body=json.dumps({
                    'status': 'error', 'error': 'Simulated upstream failure', 'phases': {},
                    'progress': 0, 'total': 1,
                }))

        self.page.route(f'**/jobs/{job.pk}/', poll)
        expect(self.page.locator('#jobError')).to_have_text('Simulated upstream failure', timeout=12000)
        expect(self.page.locator('#jobErrorPanel')).to_be_visible()
