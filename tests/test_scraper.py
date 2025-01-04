import pytest
from ski_scraper.scraper import parse_disciplines, parse_gender, parse_competition
from ski_scraper.models import Gender, Discipline
from bs4 import BeautifulSoup

@pytest.fixture
def sample_row():
    html = """
    <div class="table-row" id="55579">
        <!-- Add sample HTML structure here -->
    </div>
    """
    return BeautifulSoup(html, 'html.parser')

def test_parse_disciplines():
    assert parse_disciplines("SL GS") == [Discipline.SLALOM, Discipline.GIANT_SLALOM]
    assert parse_disciplines("DH SG") == [Discipline.DOWNHILL, Discipline.SUPER_G]
    assert parse_disciplines("GS") == [Discipline.GIANT_SLALOM]

def test_parse_gender():
    html_men = '<div class="gender"><div class="gender__item gender__item_m"></div></div>'
    html_women = '<div class="gender"><div class="gender__item gender__item_l"></div></div>'
    html_both = '<div class="gender"><div class="gender__item gender__item_m"></div><div class="gender__item gender__item_l"></div></div>'
    
    soup_men = BeautifulSoup(html_men, 'html.parser')
    soup_women = BeautifulSoup(html_women, 'html.parser')
    soup_both = BeautifulSoup(html_both, 'html.parser')
    
    assert parse_gender(soup_men) == Gender.MEN
    assert parse_gender(soup_women) == Gender.WOMEN
    assert parse_gender(soup_both) == Gender.BOTH

@pytest.mark.asyncio
async def test_scrape_competitions(sample_row):
    competition = parse_competition(sample_row)
    assert competition.event_id == "55579"
    # Add more assertions based on your sample data