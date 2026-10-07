import pytest
from radio_listener.coordinator import RadioListenerCoordinator
from radio_listener.models import EventType, ActionType

@pytest.mark.asyncio
async def test_coordinator_event_processing():
    coordinator = RadioListenerCoordinator()
    
    # 1. Simulate general rock banter -> Should not trigger alert
    alert1 = await coordinator.process_transcript_text("Playing the best modern rock on The Zone @ 91-3.")
    assert alert1 is None
    assert len(coordinator.alerts) == 0
    assert len(coordinator.transcripts) == 1

    # 2. Simulate Cue-to-Call -> Should generate actionable alert with dial link
    alert2 = await coordinator.process_transcript_text(
        "Lines are open right now! Be caller 13 at 250-475-0913 to win!"
    )
    assert alert2 is not None
    assert alert2.is_actionable_now is True
    assert alert2.event_type == EventType.CUE_TO_CALL
    assert alert2.action_type == ActionType.PHONE_CALL
    assert alert2.dial_url == "tel:2504750913"
    assert len(coordinator.alerts) == 1

    # 3. Simulate Caller Guess -> Should record to eliminated caller guesses
    alert3 = await coordinator.process_transcript_text(
        "Line 2, what's your guess for the secret? Is it the Empress Hotel? That is incorrect!"
    )
    assert alert3 is not None
    assert alert3.event_type == EventType.CALLER_GUESS
    assert len(coordinator.caller_guesses) >= 1
