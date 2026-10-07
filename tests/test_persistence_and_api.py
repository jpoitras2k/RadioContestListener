import pytest

import radio_listener.coordinator as coord_mod
from radio_listener.coordinator import RadioListenerCoordinator
from radio_listener.models import ContestClue, CallerGuess


def test_state_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(coord_mod, "STATE_FILE", tmp_path / "state.json")
    first = RadioListenerCoordinator()
    first.clues[8] = ContestClue(clue_number=8, text="Three towns that start with C's", source="manual_entry")
    first.caller_guesses.appendleft(CallerGuess(location_or_answer="Ogden Point"))
    first.save_state()

    second = RadioListenerCoordinator()
    second.load_state()
    assert second.clues[8].text == "Three towns that start with C's"
    assert second.clues[8].source == "manual_entry"
    assert second.caller_guesses[0].location_or_answer == "Ogden Point"


@pytest.mark.asyncio
async def test_manual_clue_endpoint(monkeypatch):
    from radio_listener.api import server

    async def no_send(_alert):
        return True

    monkeypatch.setattr(server.coordinator.discord_notifier, "send_alert", no_send)
    monkeypatch.setattr(server.coordinator.pushover_notifier, "send_alert", no_send)
    result = await server.add_clue_manual(server.AddCluePayload(clue_number=8, text="  Test clue  "))
    assert result["success"] is True
    assert server.coordinator.clues[8].text == "Test clue"
    assert server.coordinator.clues[8].source == "manual_entry"
