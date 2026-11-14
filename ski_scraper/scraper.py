import re
from bs4 import BeautifulSoup
import aiohttp
import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from fastapi import HTTPException
from . import models
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://www.fis-ski.com"
BASE_CALENDAR_URL = f"{BASE_URL}/DB/alpine-skiing/calendar-results.html?sectorcode=AL&categorycode="

# Cache TTLs in seconds. Short ones for pages that carry live data (live flag, run status, live results).
SHORT_CACHE_TTL = 60
FINISHED_RESULTS_CACHE_TTL = 6 * 60 * 60

class RateLimiter:
    """Token bucket: allows short bursts, then limits to `rate` requests per second"""

    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self.paused_until = 0.0
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            if time.monotonic() < self.paused_until:
                await asyncio.sleep(self.paused_until - time.monotonic())
                self.updated = time.monotonic()
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens < 1:
                await asyncio.sleep((1 - self.tokens) / self.rate)
                self.tokens = 1
                self.updated = time.monotonic()
            self.tokens -= 1

    def pause(self, seconds: float):
        """Hold back all requests, e.g. after being rate limited"""
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)
        self.tokens = 0

# FIS starts answering 429 at around 2 requests/s, so stay below that
_rate_limiter = RateLimiter(rate=1, burst=5)
RATE_LIMITED_PAUSE = 10  # seconds

_page_cache: Dict[str, tuple[float, str]] = {}  # url -> (expires_at, html)
_page_locks: Dict[str, asyncio.Lock] = {}

async def get_page_content(url: str, session: Optional[aiohttp.ClientSession] = None) -> str:
    """Fetch a page with error handling and retries"""
    if session is None:
        async with aiohttp.ClientSession() as session:
            return await get_page_content(url, session)

    max_retries = 4
    for attempt in range(max_retries):
        await _rate_limiter.acquire()
        try:
            async with session.get(url) as response:
                if response.status == 429 and attempt < max_retries - 1:
                    retry_after = response.headers.get("Retry-After", "")
                    pause = min(int(retry_after), 60) if retry_after.isdigit() else RATE_LIMITED_PAUSE
                    logger.warning(f"Rate limited by FIS, pausing all requests for {pause}s: {url}")
                    _rate_limiter.pause(pause)
                    continue  # the rate limiter waits before the retry
                response.raise_for_status()
                return await response.text()
        except aiohttp.ClientError as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"Retry {attempt + 1} after error: {e}")
            await asyncio.sleep(1 * (attempt + 1))

async def get_cached_page_content(url: str, session: Optional[aiohttp.ClientSession] = None, ttl: float = SHORT_CACHE_TTL) -> str:
    """Fetch a page, reusing a cached copy for ttl seconds. Concurrent callers share a single fetch."""
    lock = _page_locks.setdefault(url, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _page_cache.get(url)
        if cached and now < cached[0]:
            return cached[1]
        html = await get_page_content(url, session)

        # Drop expired entries so the cache doesn't grow forever
        for key in [k for k, (expires_at, _) in _page_cache.items() if expires_at <= now]:
            del _page_cache[key]
            if not _page_locks[key].locked():
                del _page_locks[key]
        _page_cache[url] = (now + ttl, html)
        return html

def parse_disciplines(text: str) -> List[models.Discipline]:
    """Parse discipline string into list of disciplines"""
    disciplines = []
    for disc in ["SL", "GS", "SG", "DH"]:
        if disc in text:
            disciplines.append(models.Discipline(disc))
    return disciplines

def parse_gender(element) -> models.Gender:
    """Parse gender from the gender element"""
    has_men = bool(element.select('.gender__item_m'))
    has_women = bool(element.select('.gender__item_l'))
    
    if has_men and has_women:
        return models.Gender.BOTH
    elif has_men:
        return models.Gender.MEN
    return models.Gender.WOMEN

def parse_status(element) -> Dict[str, bool]:
    """Parse status flags from the status element"""
    status_items = element.select('.status__item')
    return {
        'data_available': 'status__item_selected' in status_items[0].get('class', []),
        'pdf_available': 'status__item_selected' in status_items[1].get('class', []),
        'changes': 'status__item_selected' in status_items[2].get('class', []),
        'cancelled': 'status__item_selected' in status_items[3].get('class', [])
    }

def parse_competition_row(row) -> models.Competition:
    """Parse a single competition row"""
    try:
        event_id = row['id']
        
        # Get the event URL
        event_link = row.select_one('a[href*="event-details"]')
        url = event_link['href'] if event_link else None
        
        # Get basic info
        date = row.select_one('[href*="event-details"] + a').text.strip()
        is_live = date.endswith("live")
        if is_live:
            date = date[:-4].strip()
        location = row.select_one('.font_md_large').text.strip()
        country = row.select_one('.country__name-short').text.strip()
        
        # Get discipline and category
        categories = row.select('.split-row_bordered .clip')
        category = categories[0].text.strip()
        disciplines = parse_disciplines(categories[1].text.strip())
        
        # Parse gender
        gender = parse_gender(row.select_one('.gender'))
        
        # Check if cancelled
        cancelled = bool(row.select_one('.cancelled'))
        
        # Parse status flags
        status = parse_status(row.select_one('.status'))
        
        return models.Competition(
            event_id=event_id,
            date=date,
            location=location,
            country=country,
            discipline=disciplines,
            category=category,
            gender=gender,
            cancelled=cancelled,
            status=status,
            url=url,
            is_live=is_live
        )
    except Exception as e:
        logger.error(f"Error parsing competition row: {e}")
        raise

def get_season(date: datetime) -> str:
    """Get the FIS season code ('YYYY') for a date, e.g. 2026/27 -> '2027'"""
    if date.month >= 7:  # FIS seasons run from July to June
        return str(date.year + 1)
    return str(date.year)

async def scrape_competition_detail(competition_id: str, session: Optional[aiohttp.ClientSession] = None) -> models.CompetitionDetail:
    """Fetch and parse details for a specific competition"""
    url = f"{BASE_URL}/DB/general/event-details.html?sectorcode=AL&eventid={competition_id}"
    html = await get_cached_page_content(url, session)
    soup = BeautifulSoup(html, 'html.parser')
    
   # Parse races and their runs
    races = []
    race_rows = soup.select('#eventdetailscontent > .table-row')

    if len(race_rows) == 1 and race_rows[0].text.strip() == "No competition found":
        raise HTTPException(status_code=404, detail="Competition not found")
    
    for race_row in race_rows:
        # Extract race info
        codex_el = race_row.select_one('.link_theme_dark')
        if codex_el:
            codex = codex_el.select_one('.link__text').text.strip() if codex_el else None
        else:
            codex = race_row.select_one('.g-md-2').text.strip()
        
        # Extract date
        date_el = race_row.select_one('.timezone-date')
        time_el = race_row.select_one('.timezone-time')
        if date_el and time_el:
            date_str = f"{date_el['data-date']} {time_el['data-time']}"
            race_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
        elif date_el:
            date_str = date_el['data-date']
            race_date = datetime.strptime(date_str, '%Y-%m-%d')
            
        # Extract discipline and gender
        discipline_el = race_row.select_one('.g-lg-5 .clip')
        discipline_text = discipline_el.text.strip() if discipline_el else ""
        discipline = None
        for d in models.Discipline:
            if d.name == discipline_text.replace(" ", "_").upper():
                discipline = d
                break

        if not discipline:
            logger.warning(f"Unknown discipline: {discipline_text}")
            continue

        is_training = discipline_text.endswith("Training")

                
        gender_el = race_row.select_one('.gender__item')
        gender = models.Gender.WOMEN if gender_el and 'gender__item_l' in gender_el['class'] else models.Gender.MEN
        
        # Extract runs
        runs = []
        run_element = race_row.select_one('a.hidden-xs')
        race_id = re.search(r'raceid=(\d+)', run_element['href']).group(1) if re.search(r'raceid=(\d+)', run_element['href']) else None
        
        for run_el in run_element.select('.split-row_bordered .split-row__item'):
            run_info = run_el.select_one('.g-row')
            if not run_info:
                continue
            if run_el.select_one('.g-xs-24'):
                continue
                
            run_number = run_el.select_one('.split-row__item > .g-row > .g-lg-4').text.strip()
            time_el = run_info.select_one('.timezone-time')
            status_el = run_info.select_one('.g-lg-5')
            info_el = run_info.select_one('.g-lg-7')
            
            if time_el:
                time_str = time_el['data-time']
                run_time = datetime.strptime(time_str, '%H:%M').time()
            else:
                continue
                
            runs.append(models.Run(
                number=int(run_number[0]),  # Extract number from "1st", "2nd" etc
                time=run_time,
                status=status_el.text.strip() if status_el else None,
                info=info_el.text.strip() if info_el else None
            ))
            
        # Check for live timing
        live_link = race_row.select_one('a[href*="live.fis-ski.com"]')
        live_timing_url = live_link['href'] if live_link else None
            
        races.append(models.Race(
            race_id=race_id,
            codex=codex,
            date=race_date,
            discipline=discipline,
            is_training=is_training,
            gender=gender,
            runs=runs,
            has_live_timing=bool(live_timing_url),
            live_timing_url=live_timing_url
        ))

    # Parse technical delegates
    td_rows = soup.select('section:-soup-contains("Technical Delegate") .table-row')
    delegates = []
    
    for row in td_rows:
        cols = row.select('.g-xs-24 > div')
        if len(cols) < 4:
            continue
            
        delegates.append(models.TechnicalDelegate(
            codex=cols[0].text.strip(),
            name=cols[1].text.strip(),
            nation=cols[2].select_one('.country__name-short').text.strip(),
            td_id=cols[3].text.strip()
        ))

    # Parse broadcasters
    broadcaster_elements = soup.select('.broadcaster')
    broadcasters = []
    
    for br_el in broadcaster_elements:
        countries = br_el.select_one('.broadcaster-countries').text.strip().split(',')
        link_el = br_el.select_one('.broadcaster-link')
        
        broadcasters.append(models.Broadcaster(
            name=link_el.text.strip(),
            countries=[c.strip() for c in countries],
            url=link_el['href'] if link_el else None,
            logo_url=link_el.select_one('img')['src'] if link_el and link_el.select_one('img') else None
        ))

    # Parse available documents
    documents = {}
    for doc_item in soup.select('.drop-btn__item'):
        name_el = doc_item.select_one('span')
        link_el = doc_item.select_one('a')
        if name_el and link_el:
            name = re.sub(r'\s*\([^)]*\)', '', name_el.text.strip())  # Remove file size
            documents[name] = link_el['href']

    # TODO: Extract base competition info without fetching all competitions
    # Look up the calendar of the season the races belong to, so past seasons keep working
    season = get_season(races[0].date) if races else get_season(datetime.now())
    competition = None
    for category in ["WC", "WSC", "OWG"]:
        all_competitions = await list_competitions(session, category=category, season=season)
        competition = next((c for c in all_competitions if c.event_id == competition_id), None)
        if competition:
            break
        logger.debug(f"Competition {competition_id} not found in {category} calendar of season {season}")
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    # Only fetch results once we know the competition exists
    for race in races:
        # Races that haven't started yet have no results (12h margin for timezone differences)
        if not race.is_training and race.date < datetime.now() + timedelta(hours=12):
            # Results of finished races don't change anymore, so they can be cached much longer
            is_official = bool(race.runs) and all(run.status == "Official results" for run in race.runs)
            is_finished = is_official or race.date < datetime.now() - timedelta(days=2)
            cache_ttl = FINISHED_RESULTS_CACHE_TTL if is_finished else SHORT_CACHE_TTL
            race.results = await scrape_results(race.race_id, race.discipline, session, cache_ttl)

    return models.CompetitionDetail(
        competition=competition, #extract_base_competition(soup),  # You'll need to implement this
        races=races,
        technical_delegates=delegates,
        broadcasters=broadcasters,
        documents=documents
    )

async def scrape_results(race_id: str, discipline: models.Discipline, session: Optional[aiohttp.ClientSession] = None, cache_ttl: float = SHORT_CACHE_TTL) -> List[models.Result]:    
    """Fetch and parse results for a specific race"""

    if discipline in [models.Discipline.TEAM_COMBINED, models.Discipline.TEAM_PARALLEL]:
        return None

    url = f"{BASE_URL}/DB/general/results.html?sectorcode=AL&raceid={race_id}"
    html = await get_cached_page_content(url, session, cache_ttl)
    soup = BeautifulSoup(html, 'html.parser')

    # Parse results
    result_rows = soup.select('#events-info-results > .tbody > .table-row')

    if not result_rows or len(result_rows) < 2:
        return None

    results = []

    for row in result_rows:
        # Older results pages have an extra (pale) points column, skip it to keep the indexes aligned
        cols = [c for c in row.select('.g-row > .g-row > div') if 'pale' not in c.get('class', [])]
        if len(cols) < 7:
            continue

        has_two_runs = discipline in [models.Discipline.SLALOM, models.Discipline.GIANT_SLALOM]

        col_indexes = {
            'rank': 0,
            'name': 3,
            'nation': 5,
            'run1': 6 if has_two_runs else None,
            'run2': 7 if has_two_runs else None,
            'total': 8 if has_two_runs else 6,
            'diff': 9 if has_two_runs else 7,
            'fis_points': 10 if has_two_runs else 8,
            'cup_points': (11 if has_two_runs else 9) if len(cols) > 9 else None
        }

        try:
            athlete_id = re.search(r'competitorid=(\d+)', row['href']).group(1) if re.search(r'competitorid=(\d+)', row['href']) else None
            rank = int(cols[col_indexes['rank']].text.strip())
            # Newer pages split the cell into athlete and brand, older ones only contain the name
            name_el = cols[col_indexes['name']].select_one('.athlete-name') or cols[col_indexes['name']]
            name = name_el.text.strip()
            nation = cols[col_indexes['nation']].select_one('.country__name-short').text.strip()
            run1 = cols[col_indexes['run1']].text.strip() if col_indexes['run1'] else None
            run2 = cols[col_indexes['run2']].text.strip() if col_indexes['run2'] else None
            total = cols[col_indexes['total']].text.strip()
            diff = cols[col_indexes['diff']].text.strip()
            fis_points = float(cols[col_indexes['fis_points']].text.strip() or 0)
            cup_points = int(cols[col_indexes['cup_points']].text.strip() or 0) if col_indexes['cup_points'] else None

            results.append(models.Result(
                athlete_id=athlete_id,
                rank=rank,
                name=name,
                nation=nation,
                run1=run1,
                run2=run2,
                total=total,
                diff=diff,
                fis_points=fis_points,
                cup_points=cup_points
            ))
        except Exception as e:
            logger.error(f"Error parsing result row of race at url {url}: {e}")
            continue

    return results

def parse_time(time_str: str) -> Optional[datetime.time]:
    """Parse a time string like "1:23.45" into a time object"""
    try:
        minutes, seconds = map(int, time_str.split(':'))
        return datetime.time(minutes, seconds)
    except ValueError:
        return None

async def list_competitions(session: Optional[aiohttp.ClientSession] = None, category: str = "WC", season: Optional[str] = None) -> List[models.Competition]:
    """Fetch and parse the full competition list (defaults to the season FIS currently shows)"""
    url = BASE_CALENDAR_URL + category
    if season:
        url += f"&seasoncode={season}"
    html = await get_cached_page_content(url, session)
    soup = BeautifulSoup(html, 'html.parser')
    competition_rows = soup.select('.table-row')
    
    competitions = []
    for row in competition_rows:
        try:
            competition = parse_competition_row(row)
            competitions.append(competition)
        except Exception as e:
            logger.error(f"Error parsing row: {e}")
            continue
            
    return competitions