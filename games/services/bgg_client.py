import time
import math
import logging
from typing import Dict, List, Tuple
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


log = logging.getLogger(__name__)


DEFAULT_HEADERS = {
    "User-Agent": "bggweb/1.0 (+https://example.local)"
}


class BGGClient:
    def __init__(self, session: requests.Session | None = None, throttle_sec: float = 1.0):
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.throttle_sec = throttle_sec

    def _sleep(self, seconds: float | None = None):
        time.sleep(seconds if seconds is not None else self.throttle_sec)

    def _get(self, url: str, *, max_retries: int = 5, backoff: float = 2.0) -> requests.Response:
        retries = 0
        while True:
            try:
                resp = self.session.get(url, timeout=60)
                if resp.status_code == 429:
                    retries += 1
                    wait = min(10 * retries, 60)
                    log.warning("429 rate limit. sleeping %ss", wait)
                    self._sleep(wait)
                    continue
                # Special-case: BGG sometimes returns 400 with a plain text message for too many ids
                if resp.status_code == 400:
                    try:
                        body = resp.text.lower()
                    except Exception:
                        body = ''
                    if 'cannot load more than' in body:
                        return resp
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                retries += 1
                if retries > max_retries:
                    raise
                wait = backoff ** retries
                log.warning("HTTP error %s. retry %s in %ss", e, retries, wait)
                self._sleep(wait)

    # -------- Scrape Top N (ranked/advanced search approximation) --------
    def fetch_top_games_scrape(self, n: int, on_progress=None) -> Dict[str, Dict]:
        page = 1
        results: Dict[str, Dict] = {}
        while len(results) < n:
            url = (
                f"https://boardgamegeek.com/search/boardgame/page/{page}?" +
                "sort=rank&advsearch=1&" +
                "q=&include%5Bdesignerid%5D=&include%5Bpublisherid%5D=&geekitemname=&" +
                "range%5Byearpublished%5D%5Bmin%5D=&range%5Byearpublished%5D%5Bmax%5D=&" +
                "range%5Bminage%5D%5Bmax%5D=&range%5Bnumvoters%5D%5Bmin%5D=50&" +
                "range%5Bnumweights%5D%5Bmin%5D=&range%5Bminplayers%5D%5Bmax%5D=&" +
                "range%5Bmaxplayers%5D%5Bmin%5D=&range%5Bleastplaytime%5D%5Bmin%5D=&" +
                "range%5Bplaytime%5D%5Bmax%5D=&floatrange%5Bavgrating%5D%5Bmin%5D=&" +
                "floatrange%5Bavgrating%5D%5Bmax%5D=&floatrange%5Bavgweight%5D%5Bmin%5D=&" +
                "floatrange%5Bavgweight%5D%5Bmax%5D=&colfiltertype=&searchuser=&playerrangetype=normal&B1=Submit&sortdir=asc"
            )
            resp = self._get(url)
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table', {'class': 'collection_table'})
            if not table:
                break
            for row in table.find_all('tr', {'id': True}):
                tds = row.find_all('td')
                if len(tds) < 6:
                    continue
                anchor = tds[2].find('a')
                if not anchor:
                    continue
                href = anchor.get('href', '')
                title = anchor.get_text(strip=True)
                # /boardgame/XXXX/name or /boardgameexpansion/XXXX/name
                import re as _re
                m = _re.search(r'/boardgame(?:expansion)?/(\d+)', href)
                if not m:
                    continue
                gid = m.group(1)
                gtype = 'Expansion' if 'boardgameexpansion' in href else 'Base Game'
                try:
                    avg = float(tds[4].get_text(strip=True))
                except ValueError:
                    avg = None
                try:
                    voters = int(tds[5].get_text(strip=True))
                except ValueError:
                    voters = None
                if gid not in results:
                    results[gid] = {
                        'Game Title': title,
                        'Type': gtype,
                        'Game ID': gid,
                        'Average Rating': avg,
                        'Number of Voters': voters,
                        'Weight': None,
                        'Weight Votes': None,
                        'Owned': 'Not Owned',
                    }
                if len(results) >= n:
                    break
            # progress callback after each page
            if on_progress:
                try:
                    on_progress(progress=min(len(results), n), total=n)
                except Exception:
                    pass
            self._sleep(1.0)
            page += 1
        return results

    # -------- Collection API (handles 202 queueing) --------
    def fetch_owned_collection(self, username: str, on_progress=None) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        subtypes = ("boardgame", "boardgameexpansion")
        for idx, subtype in enumerate(subtypes, start=1):
            query = {
                'username': username,
                'own': 1,
                'stats': 1,
                'subtype': subtype,
            }
            url = f"https://boardgamegeek.com/xmlapi2/collection?{urlencode(query)}"
            retries = 0
            while True:
                resp = self.session.get(url, timeout=60)
                if resp.status_code == 202:
                    retries += 1
                    wait = min(5 * retries, 30)
                    log.info("Collection queued (202). Waiting %ss...", wait)
                    self._sleep(wait)
                    continue
                if resp.status_code != 200:
                    retries += 1
                    if retries > 50:
                        raise RuntimeError(f"Collection fetch failed after many retries: {resp.status_code}")
                    wait = 5
                    log.warning("Collection status %s. retry in %ss", resp.status_code, wait)
                    self._sleep(wait)
                    continue
                break
            root = ET.fromstring(resp.content)
            for item in root.findall('item'):
                gid = item.get('objectid')
                name_el = item.find('name')
                title = name_el.text if name_el is not None else ''
                stats = item.find('stats')
                avg_rating = None
                usersrated = None
                if stats is not None:
                    average = stats.find('average')
                    users = stats.find('usersrated')
                    if average is not None and average.get('value'):
                        try:
                            avg_rating = float(average.get('value'))
                        except ValueError:
                            pass
                    if users is not None and users.get('value'):
                        try:
                            usersrated = int(users.get('value'))
                        except ValueError:
                            pass
                out[gid] = {
                    'Game Title': title,
                    'Type': 'Base Game' if subtype == 'boardgame' else 'Expansion',
                    'Game ID': gid,
                    'Average Rating': avg_rating,
                    'Number of Voters': usersrated,
                    'Weight': None,
                    'Weight Votes': None,
                    'Owned': 'Owned',
                }
            # progress callback per subtype
            if on_progress:
                try:
                    on_progress(progress=idx, total=len(subtypes), items=len(out))
                except Exception:
                    pass
            self._sleep(1.5)
        return out

    # -------- Thing API (details + polls) --------
    def fetch_details_batches(self, ids: List[str], batch_size: int = 20, on_progress=None) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
        # Use a fixed batch size (default 20).
        max_ids = batch_size
        batch_size = max(1, min(batch_size, max_ids))
        updated_games: Dict[str, Dict] = {}
        player_counts: Dict[str, Dict] = {}

        idx = 0
        current_batch = batch_size
        total_ids = len(ids)
        while idx < len(ids):
            chunk = ids[idx: idx + current_batch]
            ids_param = ','.join(chunk)
            url = f"https://boardgamegeek.com/xmlapi2/thing?id={ids_param}&stats=1"
            resp = self._get(url)
            # Some errors return 200 with a text body complaining about limits
            text_lower = None
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                try:
                    text_lower = resp.text.lower()
                except Exception:
                    text_lower = ''
                if 'cannot load more than' in text_lower and len(chunk) > 1:
                    # Server refused this batch size; this shouldn't occur at 20, but surface error if it does.
                    raise RuntimeError(f"BGG refused batch size {current_batch}. Message: {text_lower[:120]}")
                # Unknown parse error; re-raise
                raise

            for item in root.findall('item'):
                gid = item.get('id')
                year = None
                yp = item.find('yearpublished')
                if yp is not None and yp.get('value'):
                    year = yp.get('value')
                ratings = item.find('./statistics/ratings')
                weight = None
                weight_votes = None
                if ratings is not None:
                    aw = ratings.find('averageweight')
                    nw = ratings.find('numweights')
                    if aw is not None and aw.get('value'):
                        try:
                            weight = round(float(aw.get('value')), 2)
                        except ValueError:
                            pass
                    if nw is not None and nw.get('value'):
                        try:
                            weight_votes = int(nw.get('value'))
                        except ValueError:
                            pass
                # boardgame rank
                bgg_rank = None
                for rank in item.findall(".//rank"):
                    if rank.get('name') == 'boardgame':
                        val = rank.get('value')
                        if val and val.isdigit():
                            bgg_rank = int(val)
                        else:
                            bgg_rank = None
                        break
                # categories
                categories = []
                for link in item.findall("link[@type='boardgamecategory']"):
                    val = link.get('value')
                    if val:
                        categories.append(val)
                # families from ranks of type 'family'
                family_map = {
                    'thematic': 'Thematic',
                    'strategygames': 'Strategy',
                    'abstracts': 'Abstract',
                    "childrensgames": "Children's Game",
                    'cgs': 'Customizable',
                    'familygames': 'Family',
                    'partygames': 'Party Game',
                    'wargames': 'Wargame',
                }
                families = []
                for fr in item.findall(".//rank[@type='family']"):
                    nm = fr.get('name')
                    if nm and nm in family_map:
                        families.append(family_map[nm])
                updated_games[gid] = {
                    'Year': year,
                    'Weight': weight,
                    'Weight Votes': weight_votes,
                    'BGG Rank': bgg_rank,
                    'Categories': categories,
                    'Families': list(sorted(set(families))),
                }
                # poll
                poll = item.find("poll[@name='suggested_numplayers']")
                pc_map: Dict[str, Dict] = {}
                if poll is not None:
                    for results in poll.findall('results'):
                        numplayers = results.get('numplayers') or ''
                        if '+' in numplayers:
                            continue
                        best = rec = notrec = 0
                        for res in results.findall('result'):
                            label = res.get('value')
                            try:
                                votes = int(res.get('numvotes') or '0')
                            except ValueError:
                                votes = 0
                            if label == 'Best':
                                best = votes
                            elif label == 'Recommended':
                                rec = votes
                            elif label == 'Not Recommended':
                                notrec = votes
                        total = best + rec + notrec
                        bp = round((best / total) * 100, 1) if total else 0.0
                        rp = round((rec / total) * 100, 1) if total else 0.0
                        np = round((notrec / total) * 100, 1) if total else 0.0
                        pc_map[numplayers] = {
                            'Best %': bp,
                            'Best Votes': best,
                            'Recommended %': rp,
                            'Recommended Votes': rec,
                            'Not Recommended %': np,
                            'Not Recommended Votes': notrec,
                            'Vote Count': total,
                        }
                player_counts[gid] = pc_map
            # After successful processing, advance window and reset batch size (in case it was reduced)
            idx += len(chunk)
            if on_progress:
                try:
                    on_progress(processed=min(idx, total_ids), total=total_ids, batch=current_batch)
                except Exception:
                    pass
            current_batch = min(batch_size, max_ids)
            self._sleep(1.0)
        return updated_games, player_counts
