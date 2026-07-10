"""Scraper for the Rally the Troops game library (rally-the-troops.com).

Rally the Troops (RTT) is a small, volunteer-run server for playing wargames
online. Its ``/games/library`` page links to one page per game, and each game
page links to that game's BoardGameGeek entry as
``boardgamegeek.com/boardgame/<id>``. We use that BGG id to match RTT games to
our catalog.

This is deliberately kept separate from ``BGGClient`` (a different host, a
different concern). It uses only the stdlib HTML parser plus ``requests`` and is
polite by default (a real User-Agent and a ~1s throttle between requests).
"""

import logging
import os
import re
import time
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.rally-the-troops.com"
LIBRARY_PATH = "/games/library"

DEFAULT_HEADERS = {
    "User-Agent": "bggweb/1.0 (+https://bggweb.onrender.com/) rally-the-troops library scraper"
}

# Single path segment of lowercase letters, digits and hyphens (a game slug).
_SLUG_RE = re.compile(r"^/([a-z0-9][a-z0-9-]*)$")
_BGG_ID_RE = re.compile(r"boardgamegeek\.com/boardgame/(\d+)")


class _LibraryParser(HTMLParser):
    """Collect game tiles from the library page.

    A game tile is an ``<a href="/<slug>">`` that wraps an ``<img>`` whose
    ``src`` points into that same ``/<slug>/`` folder (the thumbnail). That
    slug-folder match is what distinguishes real game tiles from navigation
    links, which also use single-segment hrefs but do not carry a matching
    thumbnail.
    """

    def __init__(self):
        super().__init__()
        self._slug: Optional[str] = None
        self._has_thumb = False
        self._title_parts: List[str] = []
        # Preserve library order while de-duplicating.
        self.games: "list[tuple[str, str]]" = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = (attrs.get("href") or "").strip()
            match = _SLUG_RE.match(href)
            self._slug = match.group(1) if match else None
            self._has_thumb = False
            self._title_parts = []
        elif tag == "img" and self._slug:
            src = attrs.get("src") or ""
            if f"/{self._slug}/" in src:
                self._has_thumb = True

    def handle_data(self, data):
        if self._slug:
            text = data.strip()
            if text:
                self._title_parts.append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self._slug:
            if self._has_thumb and self._slug not in self._seen:
                title = " ".join(self._title_parts).strip()
                self.games.append((self._slug, title))
                self._seen.add(self._slug)
            self._slug = None
            self._has_thumb = False
            self._title_parts = []


class RTTClient:
    def __init__(self, session: requests.Session | None = None, throttle_sec: float | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        throttle_env = os.getenv("RTT_THROTTLE_SEC")
        if throttle_sec is None and throttle_env:
            try:
                throttle_sec = float(throttle_env)
            except ValueError:
                throttle_sec = None
        self.throttle_sec = throttle_sec if throttle_sec is not None else 1.0

    def _sleep(self, seconds: float | None = None):
        time.sleep(seconds if seconds is not None else self.throttle_sec)

    def _get(self, url: str, *, max_retries: int = 4, backoff: float = 2.0) -> requests.Response:
        retries = 0
        while True:
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                retries += 1
                if retries > max_retries:
                    raise
                wait = min(backoff ** retries, 30)
                log.warning("RTT HTTP error %s. retry %s in %ss", exc, retries, wait)
                self._sleep(wait)

    def fetch_library_slugs(self) -> "list[tuple[str, str]]":
        """Return ``[(slug, title), ...]`` for every game on the library page."""
        resp = self._get(f"{BASE_URL}{LIBRARY_PATH}")
        parser = _LibraryParser()
        parser.feed(resp.text)
        if not parser.games:
            raise RuntimeError("Rally the Troops library page returned no games.")
        return parser.games

    def fetch_game_bgg_id(self, slug: str) -> Optional[str]:
        """Return the BGG id linked from a game page, or ``None`` if absent."""
        resp = self._get(f"{BASE_URL}/{slug}")
        match = _BGG_ID_RE.search(resp.text)
        return match.group(1) if match else None

    def fetch_games(
        self,
        on_progress: Optional[Callable[..., None]] = None,
        check_cancelled: Optional[Callable[[], None]] = None,
    ) -> List[Dict[str, str]]:
        """Scrape the library and each game page.

        Returns ``[{"bgg_id","slug","title"}, ...]`` for games that expose a BGG
        id. Games without a BGG link are skipped (logged). Calls
        ``on_progress(progress=, total=)`` after each game and ``check_cancelled``
        between requests so a background job can abort promptly.
        """
        if check_cancelled:
            check_cancelled()
        slugs = self.fetch_library_slugs()
        total = len(slugs)
        if on_progress:
            try:
                on_progress(progress=0, total=total)
            except Exception:
                pass

        results: List[Dict[str, str]] = []
        for idx, (slug, title) in enumerate(slugs, start=1):
            if check_cancelled:
                check_cancelled()
            self._sleep()
            try:
                bgg_id = self.fetch_game_bgg_id(slug)
            except requests.RequestException as exc:
                log.warning("RTT: failed to fetch %s: %s", slug, exc)
                bgg_id = None
            if bgg_id:
                results.append({"bgg_id": str(bgg_id), "slug": slug, "title": title})
            else:
                log.info("RTT: no BGG id found for %s; skipping", slug)
            if on_progress:
                try:
                    on_progress(progress=idx, total=total)
                except Exception:
                    pass

        if not results:
            raise RuntimeError("Rally the Troops scrape found no games with a BGG id.")
        return results
