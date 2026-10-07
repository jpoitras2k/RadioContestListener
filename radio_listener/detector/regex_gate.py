import re
from typing import Optional, Dict, Any
from radio_listener.config import settings
from radio_listener.models import EventType, ActionType

class TriggerRule:
    def __init__(self, name: str, pattern: str, event_type: EventType, action_type: ActionType):
        self.name = name
        self.regex = re.compile(pattern, re.IGNORECASE)
        self.event_type = event_type
        self.action_type = action_type

# Fast-path pre-filter rules (<0.1ms execution)
# Order matters: more specific guess interactions evaluated before general clue rules
RULES = [
    TriggerRule(
        name="caller_guess_live",
        pattern=r"\b(what['’]?s\s+your\s+guess|is\s+it\s+the|my\s+guess\s+is|guessing\s+the\s+secret|on\s+the\s+line\s+with\s+a\s+guess|that\s+is\s+(correct|incorrect|wrong))\b",
        event_type=EventType.CALLER_GUESS,
        action_type=ActionType.NONE
    ),
    TriggerRule(
        name="cue_to_call_phone",
        pattern=r"\b(call\s+(in|us|now)|dial\s+now|lines?\s+(are\s+)?open|be\s+caller|caller\s*(13|thirteen|[0-9]+)|250[-.\s]?475[-.\s]?0913|475[-.\s]?0913|1[-.\s]?888[-.\s]?414[-.\s]?0913)\b",
        event_type=EventType.CUE_TO_CALL,
        action_type=ActionType.PHONE_CALL
    ),
    TriggerRule(
        name="the_secret_clue",
        pattern=r"\b(the\s+secret|shh+|clue\s*(number|\#)?\s*\d+|new\s+clue|riddle|hickory\s+dickory|not\s+in\s+rome|giant\s+rock)\b",
        event_type=EventType.NEW_CLUE,
        action_type=ActionType.NONE
    ),
    TriggerRule(
        name="sms_keyword",
        pattern=r"\b(text\s+(in|to\s+win|and\s+win|the\s+word|keyword)|secret\s*(key)?word|code\s*word)\b",
        event_type=EventType.KEYWORD_ANNOUNCED,
        action_type=ActionType.SMS_TEXT
    )
]

class FastRegexGate:
    """
    Tier-1 In-Memory Regex Filter.
    Filters out >98% of general banter, talk, and music, preventing unneeded LLM inference.
    """
    def __init__(self):
        self.rules = RULES

    def evaluate(self, text: str) -> Optional[Dict[str, Any]]:
        text_clean = text.strip()
        if not text_clean:
            return None

        for rule in self.rules:
            match = rule.regex.search(text_clean)
            if match:
                return {
                    "matched": True,
                    "rule_name": rule.name,
                    "matched_text": match.group(0),
                    "event_type": rule.event_type,
                    "action_type": rule.action_type
                }

        return None
