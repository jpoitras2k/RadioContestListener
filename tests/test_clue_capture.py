import asyncio
import wave

import numpy as np
import pytest

import radio_listener.coordinator as coord_mod
from radio_listener.coordinator import RadioListenerCoordinator
from radio_listener.models import ContestClue


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    monkeypatch.setattr(coord_mod, "CLIPS_DIR", tmp_path)
    monkeypatch.setattr(coord_mod, "CLUE_CAPTURE_SEC", 0.2)
    c = RadioListenerCoordinator()
    for n in range(1, 7):
        c.clues[n] = ContestClue(clue_number=n, text=f"clue {n}")
    for _ in range(40):
        c._audio_ring.append(np.zeros(800, dtype=np.float32))
    return c


@pytest.mark.asyncio
async def test_chatter_about_clues_does_not_alert(coordinator):
    for line in [
        "Victoria, just awaiting the latest clue to be sent to me. He should be sending it any moment now.",
        "There was a new clue this morning with Paul and Jenny.",
        "Some of the clues have been posted on our website, and even more on a Reddit thread.",
    ]:
        assert await coordinator.process_transcript_text(line) is None
        coordinator._context_window.clear()
    assert 7 not in coordinator.clues


@pytest.mark.asyncio
async def test_unnumbered_clue_on_air_is_saved_with_audio(coordinator, tmp_path):
    alert = await coordinator.process_transcript_text("Alright, here's the new clue for The Secret.")
    assert alert is not None
    assert alert.clue_number == 7
    assert coordinator.clues[7].status == "unconfirmed"
    assert coordinator.clues[7].source == "radio_stt"

    await coordinator.process_transcript_text("Where the totems stand tall and the eagles call.")
    assert "totems stand tall" in coordinator.clues[7].text

    # A second mention shortly after extends the same clue instead of alerting again.
    assert await coordinator.process_transcript_text("Once again, that's the secret clue.") is None
    assert 8 not in coordinator.clues

    await asyncio.sleep(0.4)
    clue = coordinator.clues[7]
    assert clue.audio_url and clue.audio_url.startswith("/clips/")
    with wave.open(str(tmp_path / clue.audio_url.split("/")[-1])) as wf:
        assert wf.getframerate() == 16000
        assert wf.getnframes() > 0


@pytest.mark.asyncio
async def test_website_clue_replaces_on_air_version(coordinator, monkeypatch):
    await coordinator.process_transcript_text("Here is your clue for the secret.")
    coordinator.clues[7].audio_url = "/clips/x.wav"

    async def fake_fetch():
        return [ContestClue(clue_number=7, text="Official clue seven text")]

    async def no_sleep(_):
        coordinator.is_running = False

    monkeypatch.setattr(coordinator.scraper, "fetch_current_clues", fake_fetch)
    monkeypatch.setattr(coord_mod.asyncio, "sleep", no_sleep)
    coordinator.is_running = True
    await coordinator._run_web_watcher()

    assert coordinator.clues[7].text == "Official clue seven text"
    assert coordinator.clues[7].source == "web_scraper"
    assert coordinator.clues[7].audio_url == "/clips/x.wav"
