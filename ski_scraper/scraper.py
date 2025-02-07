import re
from bs4 import BeautifulSoup
import aiohttp
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional

from fastapi import HTTPException
from . import models
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://www.fis-ski.com"
BASE_CALENDAR_URL = f"{BASE_URL}/DB/alpine-skiing/calendar-results.html?sectorcode=AL&categorycode="

async def get_page_content(url: str, session: Optional[aiohttp.ClientSession] = None) -> str:
    """Fetch a page with error handling and retries"""
    if session is None:
        async with aiohttp.ClientSession() as session:
            return await get_page_content(url, session)
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.text()
        except aiohttp.ClientError as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"Retry {attempt + 1} after error: {e}")
            await asyncio.sleep(1 * (attempt + 1))

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

async def scrape_competition_detail(competition_id: str, session: Optional[aiohttp.ClientSession] = None) -> models.CompetitionDetail:
    """Fetch and parse details for a specific competition"""
    url = f"{BASE_URL}/DB/general/event-details.html?sectorcode=AL&eventid={competition_id}&seasoncode=2025"
    html = await get_page_content(url, session)
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
            live_timing_url=live_timing_url,
            results=await scrape_results(race_id, discipline, session) if not is_training else None
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
    all_competitions = await list_competitions(session)
    competition = next((c for c in all_competitions if c.event_id == competition_id), None)

    if not competition:
        logger.debug(f"Competition not found in list: {competition_id}, trying World Championships next")
        all_competitions = await list_competitions(session, category="WSC")
        competition = next((c for c in all_competitions if c.event_id == competition_id), None)
    if not competition:
        logger.error(f"Competition not found in list: {competition_id}, trying Winter Olympics next")
        all_competitions = await list_competitions(session, category="OWG")
        competition = next((c for c in all_competitions if c.event_id == competition_id), None)
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    return models.CompetitionDetail(
        competition=competition, #extract_base_competition(soup),  # You'll need to implement this
        races=races,
        technical_delegates=delegates,
        broadcasters=broadcasters,
        documents=documents
    )

async def scrape_results(race_id: str, discipline: models.Discipline, session: Optional[aiohttp.ClientSession] = None) -> List[models.Result]:    
    """Fetch and parse results for a specific race"""

    if discipline in [models.Discipline.TEAM_COMBINED, models.Discipline.TEAM_PARALLEL]:
        return None

    url = f"{BASE_URL}/DB/general/results.html?sectorcode=AL&raceid={race_id}"
    html = await get_page_content(url, session)
    soup = BeautifulSoup(html, 'html.parser')

    # Parse results
    result_rows = soup.select('#events-info-results > .tbody > .table-row')

    if not result_rows or len(result_rows) < 2:
        return None

    results = []

    for row in result_rows:
        cols = row.select('.g-row > .g-row > div')
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
            name = cols[col_indexes['name']].select_one('.athlete-name').text.strip()
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

async def list_competitions(session: Optional[aiohttp.ClientSession] = None, category: str = "WC") -> List[models.Competition]:
    """Fetch and parse the full competition list"""
    html = await get_page_content(BASE_CALENDAR_URL + category, session)
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