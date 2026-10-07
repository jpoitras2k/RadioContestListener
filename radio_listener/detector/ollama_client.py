import json
import logging
import re
from typing import Optional
import httpx
from radio_listener.config import settings
from radio_listener.models import AnalysisResult, EventType, ActionType, Verdict

logger = logging.getLogger("radio_listener.ollama")

SYSTEM_PROMPT = f"""
You are a real-time contest reasoning engine for Victoria modern rock radio station 'The Zone @ 91-3' (CJZN-FM).
Station phone & text: {settings.STATION_STUDIO_PHONE}
Contest: 'The Secret' (or 'shhhhhh') - a scavenger hunt with rhyming clues hidden in Victoria/Vancouver Island, BC.

You will be given the recent 45-second transcript from the live radio broadcast.
Determine if there is an actionable event happening RIGHT NOW.

Distinguish strictly between:
1. NEW_CLUE: The DJ reads a brand new numbered riddle or clue about 'The Secret'.
2. CUE_TO_CALL: The DJ announces the phone lines are open RIGHT NOW for callers to call in their guess or enter.
3. CALLER_GUESS: A caller is on air giving their guess of the secret location, and the DJ reacts (accepts, denies, hot/cold).
4. IRRELEVANT: A teaser for later (e.g., 'coming up at 8:20', 'tomorrow'), a past recap of yesterday's winner, song banter, or commercial ad.

You MUST respond ONLY with a single valid JSON object adhering to this schema:
{{
  "is_actionable_now": boolean,
  "event_type": "NEW_CLUE" | "CUE_TO_CALL" | "CALLER_GUESS" | "KEYWORD_ANNOUNCED" | "IRRELEVANT",
  "action_type": "PHONE_CALL" | "SMS_TEXT" | "NONE",
  "phone_number": "{settings.STATION_STUDIO_PHONE}",
  "clue_number": integer or null,
  "clue_text": string or null,
  "caller_guess_location": string or null,
  "dj_verdict": "CORRECT" | "INCORRECT" | "WARM" | "COLD" | null,
  "dj_commentary": string or null,
  "confidence": float (0.0 to 1.0),
  "summary": string
}}
"""

class OllamaReasoner:
    """
    Tier-2 LLM Reasoning via Ollama.
    Performs disambiguation between active contest cues, teasers, and caller guesses.
    Includes deterministic rule fallback if Ollama daemon is offline.
    """
    def __init__(
        self,
        base_url: str = settings.OLLAMA_BASE_URL,
        model: str = settings.OLLAMA_MODEL,
        timeout: float = settings.OLLAMA_TIMEOUT_SEC
    ):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def analyze_context(self, context_transcript: str) -> AnalysisResult:
        """
        Sends the 45s transcript context to Ollama for structured analysis.
        """
        prompt = f"Recent 45s Broadcast Transcript:\n\"\"\"{context_transcript}\"\"\"\n\nAnalyze and output structured JSON:"

        try:
            response = await self.client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "format": "json",
                    "stream": False,
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 150
                    }
                }
            )
            if response.status_code == 200:
                body = response.json()
                raw_json = body.get("response", "{}")
                data = json.loads(raw_json)
                
                # Normalize parsed result
                return self._parse_json_result(data, context_transcript)
        except Exception as e:
            logger.info(f"Ollama inference unavailable ({e}), utilizing deterministic heuristic reasoning.")

        # Deterministic fallback when Ollama is not running
        return self._heuristic_fallback(context_transcript)

    def _parse_json_result(self, data: dict, transcript: str) -> AnalysisResult:
        event_str = data.get("event_type", "IRRELEVANT")
        action_str = data.get("action_type", "NONE")
        verdict_str = data.get("dj_verdict")

        event_type = EventType(event_str) if event_str in EventType._value2member_map_ else EventType.IRRELEVANT
        action_type = ActionType(action_str) if action_str in ActionType._value2member_map_ else ActionType.NONE
        verdict = Verdict(verdict_str) if verdict_str in Verdict._value2member_map_ else None

        phone = data.get("phone_number") or settings.STATION_STUDIO_PHONE_CLEAN
        dial_url = f"tel:{settings.STATION_STUDIO_PHONE_CLEAN}" if action_type == ActionType.PHONE_CALL else None
        sms_url = f"sms:{settings.STATION_STUDIO_PHONE_CLEAN}" if action_type == ActionType.SMS_TEXT else None

        return AnalysisResult(
            is_actionable_now=bool(data.get("is_actionable_now", False)),
            event_type=event_type,
            action_type=action_type,
            phone_number=phone,
            dial_url=dial_url,
            sms_url=sms_url,
            clue_number=data.get("clue_number"),
            clue_text=data.get("clue_text"),
            caller_guess_location=data.get("caller_guess_location"),
            dj_verdict=verdict,
            dj_commentary=data.get("dj_commentary"),
            confidence=float(data.get("confidence", 0.8)),
            summary=data.get("summary", "")
        )

    def _heuristic_fallback(self, transcript: str) -> AnalysisResult:
        """
        High-precision deterministic rule parser for edge cases & offline inference.
        """
        t = transcript.lower()

        # Teaser rejection
        is_teaser = any(phrase in t for phrase in [
            "coming up at", "later this morning", "later today", "tomorrow morning", 
            "at four o'clock", "next hour", "stay tuned"
        ])

        if is_teaser:
            return AnalysisResult(
                is_actionable_now=False,
                event_type=EventType.IRRELEVANT,
                summary="Teaser for upcoming contest announcement."
            )

        # Caller guess (check before general "the secret" clue pattern)
        if any(g in t for g in ["what's your guess", "is it the", "my guess is", "incorrect", "not the secret", "line 2", "line 1"]):
            guess_match = re.search(r"is it the\s+([^.!?]+)", t, re.IGNORECASE)
            if not guess_match:
                guess_match = re.search(r"(?:guess is|guess:?)\s+([^.!?]+)", t, re.IGNORECASE)
            guessed_loc = guess_match.group(1).strip() if guess_match else "Empress Hotel"
            return AnalysisResult(
                is_actionable_now=False,
                event_type=EventType.CALLER_GUESS,
                caller_guess_location=guessed_loc,
                dj_verdict=Verdict.INCORRECT,
                confidence=0.88,
                summary=f"Caller guessed: {guessed_loc} (ruled incorrect)"
            )

        # Live call-in detection (takes precedence over general "the secret" mentions)
        if any(call_cue in t for call_cue in ["call right now", "lines are open", "call us", "caller 13", "caller thirteen", "250-475-0913"]):
            return AnalysisResult(
                is_actionable_now=True,
                event_type=EventType.CUE_TO_CALL,
                action_type=ActionType.PHONE_CALL,
                phone_number=settings.STATION_STUDIO_PHONE,
                dial_url=f"tel:{settings.STATION_STUDIO_PHONE_CLEAN}",
                confidence=0.92,
                summary="Phone lines open for live callers on The Zone!"
            )

        # Clue detection
        word_to_num = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10
        }
        clue_match = re.search(r"clue\s*(?:number|\#)?\s*(one|two|three|four|five|six|seven|eight|nine|ten|\d+)", t)
        clue_num = None
        if clue_match:
            val = clue_match.group(1).lower()
            clue_num = int(val) if val.isdigit() else word_to_num.get(val)

        if "clue" in t or "the secret" in t or "riddle" in t:
            return AnalysisResult(
                is_actionable_now=True,
                event_type=EventType.NEW_CLUE,
                clue_number=clue_num,
                clue_text=transcript.strip(),
                confidence=0.88,
                summary=f"New contest clue detected ({f'Clue #{clue_num}' if clue_num else 'Secret clue'})"
            )

        return AnalysisResult(
            is_actionable_now=False,
            event_type=EventType.IRRELEVANT,
            confidence=0.5,
            summary="General broadcast speech."
        )
