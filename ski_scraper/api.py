import logging
from typing import Optional
import aiohttp
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
from . import scraper
from . import models

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ski Competition Scraper API",
    description="API for retrieving structured ski competition data",
    version="0.1.0"
)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/api/v1/competitions/{competition_id}")
async def get_competition(competition_id: str):
    try:
        async with aiohttp.ClientSession() as session:
            data = await scraper.scrape_competition_detail(competition_id, session=session)
        return data
    except Exception as e:
        logger.error(f"Error fetching competition details", exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/competitions")
async def list_competitions(
    gender: Optional[str] = None,
    discipline: Optional[str] = None,
    location: Optional[str] = None
):
    """Get list of competitions with optional filters"""
    try:
        async with aiohttp.ClientSession() as session:
            competitions = await scraper.list_competitions(session=session)
        
        # Apply filters
        if gender:
            competitions = [c for c in competitions if c.gender == gender]
        if discipline:
            competitions = [c for c in competitions if discipline in c.discipline]
        if location:
            competitions = [c for c in competitions if location.lower() in c.location.lower()]
            
        return competitions
    except Exception as e:
        logger.error(f"Error listing competitions", exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))