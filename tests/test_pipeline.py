import pytest
import numpy as np
from radio_listener.vad.silero_gate import VoiceActivityDetector
from radio_listener.detector.regex_gate import FastRegexGate
from radio_listener.models import EventType, ActionType
from radio_listener.detector.ollama_client import OllamaReasoner

def test_vad_speech_gating():
    vad = VoiceActivityDetector(min_speech_ms=50, min_silence_ms=100, max_duration_sec=2.0)
    
    # 50ms frames at 16kHz = 800 samples
    silent_frame = np.zeros(800, dtype=np.float32)
    loud_speech_frame = (np.sin(np.linspace(0, 50, 800)) * 0.15).astype(np.float32)

    # Initially quiet
    res = vad.process_frame(silent_frame)
    assert res is None

    # Continuous speech for 4 frames (200ms)
    for _ in range(4):
        vad.process_frame(loud_speech_frame)
    assert vad.is_speaking is True

    # Silence pause to trigger completion (min_silence_frames = 2)
    vad.process_frame(silent_frame)
    final_utterance = vad.process_frame(silent_frame)
    
    assert final_utterance is not None
    assert len(final_utterance) > 0
    assert vad.is_speaking is False

def test_tier1_regex_triggers():
    gate = FastRegexGate()

    # Call-in trigger with phone number
    match = gate.evaluate("Be caller 13 right now at 250-475-0913 to win!")
    assert match is not None
    assert match["event_type"] == EventType.CUE_TO_CALL
    assert match["action_type"] == ActionType.PHONE_CALL

    # The Secret clue trigger
    match2 = gate.evaluate("Here is clue number five for The Secret: I'm high up in the sky.")
    assert match2 is not None
    assert match2["event_type"] == EventType.NEW_CLUE

    # Live caller guess trigger
    match3 = gate.evaluate("Caller on line 1, what's your guess for the secret?")
    assert match3 is not None
    assert match3["event_type"] == EventType.CALLER_GUESS

    # Normal rock music / banter (should be skipped)
    match4 = gate.evaluate("Coming up next, Green Day on The Zone 91-3.")
    assert match4 is None

@pytest.mark.asyncio
async def test_ollama_reasoner_heuristic_fallback():
    reasoner = OllamaReasoner()

    # Teaser scenario
    teaser_result = await reasoner.analyze_context(
        "Coming up at eight twenty this morning, we're gonna have your next clue for the secret."
    )
    assert teaser_result.is_actionable_now is False
    assert teaser_result.event_type == EventType.IRRELEVANT

    # Live call-in scenario
    callin_result = await reasoner.analyze_context(
        "Phone lines are open right now! Call 250-475-0913 and be caller 13!"
    )
    assert callin_result.is_actionable_now is True
    assert callin_result.event_type == EventType.CUE_TO_CALL
    assert callin_result.action_type == ActionType.PHONE_CALL
    assert "2504750913" in (callin_result.dial_url or "")

    # Clue extraction scenario
    clue_result = await reasoner.analyze_context(
        "Listen closely, clue number 5 for The Secret: I sit by the harbor lights."
    )
    assert clue_result.is_actionable_now is True
    assert clue_result.event_type == EventType.NEW_CLUE
    assert clue_result.clue_number == 5

    # Clue extraction with word numbers ("clue number seven")
    clue7_result = await reasoner.analyze_context(
        "Here is clue number seven for The Secret: I stand where the ferries depart from the bay."
    )
    assert clue7_result.is_actionable_now is True
    assert clue7_result.event_type == EventType.NEW_CLUE
    assert clue7_result.clue_number == 7
