import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from radio_listener.config import settings
from radio_listener.coordinator import RadioListenerCoordinator
from radio_listener.models import (
    ContestAlert, ContestClue, CallerGuess, TranscriptSegment,
    EventType, ActionType, Verdict
)

logger = logging.getLogger("radio_listener.api")

coordinator = RadioListenerCoordinator()

app = FastAPI(
    title="The Zone @ 91-3 Contest Listener & Radar",
    description="Real-time radio contest monitoring and Pushover alerting pipeline"
)

# Active WebSocket connections
connected_websockets: List[WebSocket] = []

def ws_broadcaster(payload: Dict[str, Any]):
    # Broadcast asynchronously to all active websocket clients
    asyncio.create_task(_send_ws(payload))

async def _send_ws(payload: Dict[str, Any]):
    dead_sockets = []
    for ws in connected_websockets:
        try:
            await ws.send_json(payload)
        except Exception:
            dead_sockets.append(ws)
    for ds in dead_sockets:
        if ds in connected_websockets:
            connected_websockets.remove(ds)

coordinator.subscribe(ws_broadcaster)

@app.on_event("startup")
async def startup_event():
    await coordinator.start()

@app.on_event("shutdown")
async def shutdown_event():
    await coordinator.stop()

@app.get("/api/status")
async def get_status():
    return {
        "status": "running" if coordinator.is_running else "stopped",
        "station": settings.STATION_NAME,
        "callsign": settings.STATION_CALLSIGN,
        "frequency": f"{settings.STATION_FREQUENCY_MHZ} MHz",
        "studio_phone": settings.STATION_STUDIO_PHONE,
        "total_audio_frames": coordinator.total_audio_frames_processed,
        "total_speech_segments": coordinator.total_speech_segments,
        "total_triggers_fired": coordinator.total_triggers_fired,
        "pushover_enabled": settings.PUSHOVER_ENABLED,
        "discord_enabled": settings.DISCORD_ENABLED and bool(coordinator.discord_notifier.webhook_url)
    }

class DiscordConfigPayload(BaseModel):
    webhook_url: str

@app.post("/api/config/discord")
async def config_discord(payload: DiscordConfigPayload):
    coordinator.discord_notifier.webhook_url = payload.webhook_url.strip()
    coordinator.discord_notifier.enabled = True
    settings.DISCORD_WEBHOOK_URL = payload.webhook_url.strip()
    settings.DISCORD_ENABLED = True
    
    # Send a verification / welcome alert to the channel
    test_alert = ContestAlert(
        id="test-connection",
        event_type=EventType.IRRELEVANT,
        action_type=ActionType.NONE,
        title="✅ Discord Webhook Connected Successfully!",
        summary="The Zone @ 91-3 Contest Radar is now connected to this channel. You will receive real-time alerts for live caller cues, new clues for 'The Secret', and ruled-out caller guesses here.",
        is_actionable_now=False,
        phone_number=settings.STATION_STUDIO_PHONE
    )
    success = await coordinator.discord_notifier.send_alert(test_alert)
    return {"success": success, "message": "Discord webhook updated and test message sent."}

@app.get("/api/clues")
async def get_clues():
    return sorted(list(coordinator.clues.values()), key=lambda c: c.clue_number)

@app.get("/api/alerts")
async def get_alerts():
    return list(coordinator.alerts)

@app.get("/api/guesses")
async def get_guesses():
    return list(coordinator.caller_guesses)

@app.get("/api/transcripts")
async def get_transcripts():
    return list(coordinator.transcripts)

@app.post("/api/scrape_now")
async def scrape_now():
    clues = await coordinator.scraper.fetch_current_clues()
    for c in clues:
        coordinator.clues[c.clue_number] = c
    return {"message": "Scraped successfully", "clues_count": len(coordinator.clues)}

class CustomTranscriptPayload(BaseModel):
    text: str

@app.post("/api/simulate/custom")
async def simulate_custom(payload: CustomTranscriptPayload):
    alert = await coordinator.process_transcript_text(payload.text)
    return {"processed": True, "alert_triggered": alert is not None, "alert": alert}

@app.post("/api/simulate/{scenario}")
async def simulate_scenario(scenario: str):
    scenarios = {
        "call_in": (
            "Alright Zone listeners, this is Pol and Jenny on the Morning Zone! "
            "The phone lines are open right now. Be caller thirteen at 250-475-0913 "
            "to give your guess for The Secret!"
        ),
        "new_clue": (
            "We have a brand new clue for The Secret! Clue number five: "
            "I stand where the ferries depart from the bay, watching the islands both night and day."
        ),
        "caller_guess": (
            "We have Sarah on line two. Sarah, what's your guess for the secret? "
            "Is it the Ogden Point Breakwater? Oh Sarah, unfortunately that is incorrect, not the breakwater!"
        ),
        "teaser": (
            "Coming up at eight twenty this morning, we're gonna have your next clue "
            "for the secret cash prize. Stay tuned to The Zone 91-3!"
        ),
        "banter": (
            "That was Foo Fighters with Everlong right here on Victoria's modern rock, "
            "The Zone @ 91-3. It is currently fourteen degrees downtown."
        )
    }

    if scenario not in scenarios:
        raise HTTPException(status_code=400, detail="Unknown scenario")

    text = scenarios[scenario]
    alert = await coordinator.process_transcript_text(text)
    return {"scenario": scenario, "text": text, "alert_triggered": alert is not None, "alert": alert}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in connected_websockets:
            connected_websockets.remove(websocket)

# Serve dashboard
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
async def root():
    if INDEX_HTML.exists():
        return FileResponse(str(INDEX_HTML))
    return {"message": "The Zone @ 91-3 Radio Contest Listener API running"}
