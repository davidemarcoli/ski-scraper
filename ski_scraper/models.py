from pydantic import BaseModel, HttpUrl
from typing import Dict, List, Optional
from datetime import datetime, time
from enum import Enum

class Gender(str, Enum):
    MEN = "M"
    WOMEN = "W"
    BOTH = "BOTH"

class Discipline(str, Enum):
    SLALOM = "SL"
    GIANT_SLALOM = "GS" 
    SUPER_G = "SG"
    DOWNHILL = "DH"
    SLALOM_TRAINING = "SLT"
    GIANT_SLALOM_TRAINING = "GST"
    SUPER_G_TRAINING = "SGT"
    DOWNHILL_TRAINING = "DHT"

class Competition(BaseModel):
    event_id: str
    date: str  # Range like "26-27 Oct 2024"
    location: str
    country: str  # NSA country code
    discipline: List[Discipline]  
    category: str  # WC, TRA • WC etc
    gender: Gender
    cancelled: bool = False
    status: dict = {} # D, P, C status flags
    is_live: bool = False

class CompetitionList(BaseModel):
    competitions: List[Competition]

class Run(BaseModel):
    number: int  # 1st, 2nd etc
    time: time   # Start time
    status: Optional[str] = None  # e.g. "Official results"
    info: Optional[str] = None

class Result(BaseModel):
    athlete_id: str
    rank: int
    name: str
    nation: str
    run1: Optional[str] = None
    run2: Optional[str] = None
    total: Optional[str] = None
    diff: Optional[str] = None
    fis_points: Optional[float] = None
    cup_points: Optional[int] = None

class Race(BaseModel):
    race_id: str
    codex: str  # e.g. "5001" or "0001"
    date: datetime
    discipline: Discipline
    is_training: bool = False
    gender: Gender
    runs: List[Run]
    has_live_timing: bool = False
    live_timing_url: Optional[HttpUrl] = None
    results: Optional[List[Result]] = None

class TechnicalDelegate(BaseModel):
    codex: str
    name: str
    nation: str
    td_id: str

class Broadcaster(BaseModel):
    name: str
    countries: List[str]
    url: Optional[HttpUrl] = None
    logo_url: Optional[HttpUrl] = None

class CompetitionDetail(BaseModel):
    competition: Optional[Competition]  # Base competition info    # TODO: doesn't need to be optional
    races: List[Race]
    technical_delegates: List[TechnicalDelegate]
    broadcasters: List[Broadcaster]
    documents: Dict[str, str]  # Document name -> URL mapping