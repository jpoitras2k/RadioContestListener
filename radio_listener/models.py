from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class EventType(str, Enum):
    NEW_CLUE = "NEW_CLUE"
    CUE_TO_CALL = "CUE_TO_CALL"
    CALLER_GUESS = "CALLER_GUESS"
    KEYWORD_ANNOUNCED = "KEYWORD_ANNOUNCED"
    IRRELEVANT = "IRRELEVANT"

class Verdict(str, Enum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    WARM = "WARM"
    COLD = "COLD"
    UNKNOWN = "UNKNOWN"

class ActionType(str, Enum):
    PHONE_CALL = "PHONE_CALL"
    SMS_TEXT = "SMS_TEXT"
    NONE = "NONE"

class ContestClue(BaseModel):
    clue_number: int
    text: str
    discovered_at: datetime = Field(default_factory=utc_now)
    source: str = "web_scraper" # "web_scraper" | "radio_stt"
    status: str = "active"

class CallerGuess(BaseModel):
    caller_id: Optional[str] = None
    location_or_answer: str
    verdict: Verdict = Verdict.INCORRECT
    notes: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)

class TranscriptSegment(BaseModel):
    text: str
    start_time: float
    end_time: float
    confidence: float
    timestamp: datetime = Field(default_factory=utc_now)
    is_trigger: bool = False

class ContestAlert(BaseModel):
    id: str
    event_type: EventType
    action_type: ActionType
    title: str
    summary: str
    is_actionable_now: bool = False
    phone_number: Optional[str] = None
    dial_url: Optional[str] = None
    sms_url: Optional[str] = None
    clue_number: Optional[int] = None
    clue_text: Optional[str] = None
    caller_guess: Optional[str] = None
    verdict: Optional[Verdict] = None
    confidence: float = 1.0
    timestamp: datetime = Field(default_factory=utc_now)
    raw_transcript_window: Optional[str] = None

class AnalysisResult(BaseModel):
    is_actionable_now: bool
    event_type: EventType
    action_type: ActionType = ActionType.NONE
    phone_number: Optional[str] = None
    dial_url: Optional[str] = None
    sms_url: Optional[str] = None
    clue_number: Optional[int] = None
    clue_text: Optional[str] = None
    caller_guess_location: Optional[str] = None
    dj_verdict: Optional[Verdict] = None
    dj_commentary: Optional[str] = None
    confidence: float = 0.5
    summary: str = ""
