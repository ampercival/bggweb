import time
import logging
import os
from typing import Dict, List, Tuple, Iterator
from urllib.parse import urlencode, urljoin
import csv
import io
import zipfile
import requests
import xml.etree.ElementTree as ET
from games.forms import validate_ranks_url


log = logging.getLogger(__name__)


DEFAULT_HEADERS = {
    "User-Agent": "bggweb/1.0 (+https://bggweb.onrender.com/)"
}

# BGG boardgamefamily "Digital Implementations: Board Game Arena": the family
# whose member games are the ones playable on Board Game Arena.
BGA_FAMILY_ID = "70360"




def _validate_xml(root):
    if root.tag != 'items' or root.find('.//error') is not None:
        raise RuntimeError("BoardGameGeek returned an error instead of game data.")


class BGGClient:
    def __init__(self, session: requests.Session | None = None, throttle_sec: float | None = None, detail_throttle_sec: float | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        throttle_env = os.getenv("BGG_THROTTLE_SEC")
        detail_env = os.getenv("BGG_DETAILS_THROTTLE_SEC")
        if throttle_sec is None and throttle_env:
            try:
                throttle_sec = float(throttle_env)
            except ValueError:
                throttle_sec = None
        if detail_throttle_sec is None and detail_env:
            try:
                detail_throttle_sec = float(detail_env)
            except ValueError:
                detail_throttle_sec = None
        self.throttle_sec = throttle_sec if throttle_sec is not None else 1.5
        default_detail = max(self.throttle_sec, 2.5)
        self.detail_throttle_sec = (
            detail_throttle_sec            if detail_throttle_sec is not None else default_detail
        )
        
        token = os.getenv("BGG_API_TOKEN")
        if token:
            self.api_headers = {"Authorization": f"Bearer {token}"}
        else:
            self.api_headers = {}
            log.warning("BGG_API_TOKEN not found in environment variables. API calls may fail.")

    def _check(self):
        callback = getattr(self, 'check_cancelled', None)
        if callback:
            callback()

    def _sleep(self, seconds: float | None = None):
        remaining = max(0, seconds if seconds is not None else self.throttle_sec)
        while remaining > 0:
            self._check()
            step = min(remaining, 1.0)
            time.sleep(step)
            remaining -= step
        self._check()

    def _get(
        self,
        url: str,
        *,
        headers: Dict | None = None,
        max_retries: int = 5,
        backoff: float = 2.0,
        fatal_statuses: set[int] | None = None,
        ranks_download: bool = False,
    ) -> requests.Response:
        retries = 0
        rate_limit_retries = 0
        max_rate_retries = max(max_retries * 2, 6)
        while True:
            self._check()
            try:
                if ranks_download:
                    download_url = url
                    for _ in range(5):
                        validate_ranks_url(download_url)
                        resp = self.session.get(download_url, headers=headers, timeout=60,
                                                stream=True, allow_redirects=False)
                        if resp.status_code not in (301, 302, 303, 307, 308):
                            break
                        destination = urljoin(download_url, resp.headers.get('Location', ''))
                        resp.close()
                        download_url = destination
                    else:
                        raise RuntimeError('Too many ranks download redirects.')
                    try:
                        content, size = [], 0
                        for block in resp.iter_content(chunk_size=65536):
                            self._check()
                            size += len(block)
                            if size > 50 * 1024 * 1024:
                                raise RuntimeError('Ranks ZIP exceeds the 50 MB download limit.')
                            content.append(block)
                        resp._content = b''.join(content)
                    finally:
                        resp.close()
                else:
                    resp = self.session.get(url, headers=headers, timeout=60)
                if resp.status_code == 429:
                    rate_limit_retries += 1
                    retry_after = resp.headers.get("Retry-After")
                    wait = None
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            wait = None
                    if wait is None:
                        wait = min(15 * rate_limit_retries, 90)
                    log.warning(
                        "429 rate limit from BGG (attempt %s). Sleeping %ss",
                        rate_limit_retries,
                        wait,
                    )
                    self._sleep(min(max(wait, 0), 120))
                    if rate_limit_retries >= max_rate_retries:
                        raise RuntimeError(
                            "BGG rate limit hit repeatedly while fetching data."
                        )
                    continue
                rate_limit_retries = 0
                if fatal_statuses and resp.status_code in fatal_statuses:
                    log.error("Fatal HTTP status %s for %s; aborting request.", resp.status_code, url)
                    raise RuntimeError(
                        "BoardGameGeek ranks download returned HTTP %s. "
                        "Generate a fresh data-dump link and try again." % resp.status_code
                    )
                if resp.status_code in (400, 401, 403, 404):
                    if resp.status_code != 400 or 'cannot load more than' not in resp.text.lower():
                        raise RuntimeError(f'BGG returned HTTP {resp.status_code}. Check credentials and request inputs.')
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
                wait = min(backoff ** retries, 120)
                log.warning("HTTP error %s. retry %s in %ss", e, retries, wait)
                self._sleep(wait)


    # -------- Scrape Top N (ranked/advanced search approximation) --------
    def fetch_top_games_ranks(self, n: int, on_progress=None, zip_url: str | None = None) -> Dict[str, Dict]:
        if not zip_url:
            raise RuntimeError('A ranks ZIP URL is required. Paste the "Click to Download" link from the BGG data dumps page.')
        try:
            zip_resp = self._get(zip_url, fatal_statuses={403}, ranks_download=True)
        except RuntimeError as exc:
            raise RuntimeError('Failed to download ranks ZIP. The link may have expired or is invalid.') from exc
        except requests.RequestException as exc:
            raise RuntimeError('Failed to download ranks ZIP. The link may have expired.') from exc

        try:
            with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
                members = zf.namelist()
                csv_name = next((name for name in members if name.lower().endswith('.csv')), None)
                if not csv_name:
                    raise RuntimeError('Ranks archive did not contain a CSV file')
                if zf.getinfo(csv_name).file_size > 250 * 1024 * 1024:
                    raise RuntimeError('Ranks CSV exceeds the 250 MB uncompressed limit.')
                with zf.open(csv_name) as csvfile:
                    reader = csv.DictReader(io.TextIOWrapper(csvfile, encoding='utf-8'))
                    results: Dict[str, Dict] = {}
                    processed = 0
                    limit = None if n <= 0 else n
                    for row in reader:
                        try:
                            rank_val = int(row.get('rank') or 0)
                        except (TypeError, ValueError):
                            continue
                        if rank_val <= 0:
                            continue
                        gid = row.get('id') or ''
                        name = row.get('name') or ''
                        if not gid or not name:
                            continue
                        def to_float(key):
                            try:
                                val = row.get(key)
                                return float(val) if val not in (None, '', 'null') else None
                            except (TypeError, ValueError):
                                return None
                        def to_int(key):
                            try:
                                val = row.get(key)
                                return int(val) if val not in (None, '', 'null') else None
                            except (TypeError, ValueError):
                                return None
                        avg_rating = to_float('average')
                        voters = to_int('usersrated')
                        results[gid] = {
                            'Game Title': name,
                            'Game ID': gid,
                            'Type': 'Expansion' if (row.get('is_expansion') in ('1', 'true', 'True')) else 'Base Game',
                            'Average Rating': avg_rating,
                            'Number of Voters': voters,
                            'Weight': None,
                            'Weight Votes': None,
                            'BGG Rank': rank_val,
                            'Owned': 'Not Owned',
                        }
                        processed += 1
                        if on_progress and limit is not None and processed % 200 == 0:
                            on_progress(progress=processed, total=limit)
                        if limit is not None and processed >= limit:
                            break
        except zipfile.BadZipFile as exc:
            raise RuntimeError('Ranks ZIP was invalid or corrupted.') from exc

        if not results:
            raise RuntimeError('Ranks CSV did not contain any ranked games.')

        if on_progress:
            target = (n if n > 0 else processed) or processed
            on_progress(progress=processed, total=target)
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
            for attempt in range(12):
                self._check()
                resp = self._get(url, headers=self.api_headers, max_retries=3)
                if resp.status_code == 200:
                    break
                if resp.status_code != 202:
                    raise RuntimeError(f'Unexpected collection response: HTTP {resp.status_code}')
                if attempt == 11:
                    raise RuntimeError('BGG collection is still queued after 12 attempts. Please retry later.')
                self._sleep(min(5 * (attempt + 1), 30))
            root = ET.fromstring(resp.content)
            _validate_xml(root)
            for item in root.findall('item'):
                gid = item.get('objectid')
                if not gid:
                    raise RuntimeError('Collection item is missing its game ID.')
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
                on_progress(progress=idx, total=len(subtypes), items=len(out))
            self._sleep(1.5)
        return out

    # -------- Family API (member games of a boardgamefamily) --------
    def fetch_family_members(self, family_id: str, on_progress=None) -> List[Dict[str, str]]:
        """Return ``[{'bgg_id','title'}, ...]`` for every game in a BGG family.

        Uses XML API2 ``family?id=`` and collects the item's inbound links, which
        are the member games. Like the other ``thing``/``collection`` calls this
        sends the optional ``BGG_API_TOKEN`` bearer header; without a valid token
        BGG typically responds 401.
        """
        url = f"https://boardgamegeek.com/xmlapi2/family?id={family_id}"
        try:
            resp = self._get(url, headers=self.api_headers, max_retries=3)
        except requests.RequestException as exc:
            raise RuntimeError(
                "Failed to fetch the BGG family. If BGG returns 401 Unauthorized, "
                "set BGG_API_TOKEN in the environment."
            ) from exc

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise RuntimeError(
                "BGG family response was not valid XML (check BGG_API_TOKEN and the family id)."
            ) from exc

        _validate_xml(root)
        results: List[Dict[str, str]] = []
        seen: set[str] = set()
        for item in root.findall("item"):
            for link in item.findall("link"):
                if link.get("inbound") != "true":
                    continue
                gid = link.get("id")
                if not gid or gid in seen:
                    continue
                seen.add(gid)
                results.append({"bgg_id": str(gid), "title": link.get("value") or ""})

        if not results:
            raise RuntimeError(f"BGG family {family_id} returned no linked games.")
        if on_progress:
            on_progress(progress=len(results), total=len(results))
        return results

    # -------- Thing API (details + polls) --------
    def _iter_details_batches(
        self,
        ids: List[str],
        batch_size: int = 20,
        on_progress=None,
    ) -> Iterator[Tuple[List[str], Dict[str, Dict], Dict[str, Dict]]]:
        batch_size = max(1, batch_size)
        idx = 0
        current_batch = batch_size
        total_ids = len(ids)
        queue_retries = 0
        while idx < len(ids):
            chunk = ids[idx: idx + current_batch]
            ids_param = ','.join(chunk)
            url = f"https://boardgamegeek.com/xmlapi2/thing?id={ids_param}&stats=1"
            resp = self._get(url, headers=self.api_headers, max_retries=8)
            text_lower = None
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                try:
                    text_lower = resp.text.lower()
                except Exception:
                    text_lower = ''
                if 'cannot load more than' in text_lower and len(chunk) > 1:
                    raise RuntimeError(f"BGG refused batch size {current_batch}. Message: {text_lower[:120]}")
                raise

            items = root.findall('item')
            if not items:
                message_node = root if root.tag == 'message' else root.find('message')
                message_text = (message_node.text or '').strip() if message_node is not None else ''
                lower_message = message_text.lower()
                is_queue = resp.status_code == 202 or 'try again' in lower_message or 'processing' in lower_message or 'queued' in lower_message
                if message_text and is_queue:
                    queue_retries += 1
                    wait = min(5 + queue_retries * 5, 60)
                    log.info(
                        "BGG queueing details for %s ids (attempt %s). Waiting %ss",
                        len(chunk),
                        queue_retries,
                        wait,
                    )
                    if on_progress:
                        on_progress(status="waiting")
                    self._sleep(wait)
                    if queue_retries >= 12:
                        snippet = message_text[:120]
                        raise RuntimeError(
                            f"BGG is still preparing game details after multiple attempts: {snippet or 'please retry later.'}"
                        )
                    continue
            _validate_xml(root)
            if {item.get('id') for item in items} != set(chunk):
                raise RuntimeError("Incomplete BGG details response; please retry later.")
            queue_retries = 0

            chunk_details: Dict[str, Dict] = {}
            chunk_counts: Dict[str, Dict] = {}
            for item in items:
                gid = item.get('id')
                if not gid:
                    continue
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
                bgg_rank = None
                for rank in item.findall(".//rank"):
                    if rank.get('name') == 'boardgame':
                        val = rank.get('value')
                        if val and val.isdigit():
                            bgg_rank = int(val)
                        else:
                            bgg_rank = None
                        break
                categories = []
                for link in item.findall("link[@type='boardgamecategory']"):
                    val = link.get('value')
                    if val:
                        categories.append(val)
                mechanics = []
                for link in item.findall("link[@type='boardgamemechanic']"):
                    val = link.get('value')
                    if val:
                        mechanics.append(val)
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
                chunk_details[gid] = {
                    'Year': year,
                    'Weight': weight,
                    'Weight Votes': weight_votes,
                    'BGG Rank': bgg_rank,
                    'Categories': categories,
                    'Mechanics': mechanics,
                    'Families': list(sorted(set(families))),
                }
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
                            'Rec. %': rp,
                            'Rec. Votes': rec,
                            'Not %': np,
                            'Not Votes': notrec,
                            'Total Votes': total,
                        }
                chunk_counts[gid] = pc_map

            idx += len(chunk)
            if on_progress:
                on_progress(processed=min(idx, total_ids), total=total_ids, batch=current_batch, status="running")
            self._check()
            self._sleep(self.detail_throttle_sec)
            yield chunk, chunk_details, chunk_counts

    def fetch_details_batches(self, ids: List[str], batch_size: int = 20, on_progress=None) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
        updated_games: Dict[str, Dict] = {}
        player_counts: Dict[str, Dict] = {}
        for chunk_ids, chunk_details, chunk_counts in self._iter_details_batches(ids, batch_size=batch_size, on_progress=on_progress):
            updated_games.update(chunk_details)
            player_counts.update(chunk_counts)
        return updated_games, player_counts

    def stream_details_batches(self, ids: List[str], batch_size: int = 20, on_progress=None) -> Iterator[Tuple[List[str], Dict[str, Dict], Dict[str, Dict]]]:
        yield from self._iter_details_batches(ids, batch_size=batch_size, on_progress=on_progress)

