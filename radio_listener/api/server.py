import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import httpx
from pydantic import BaseModel

from radio_listener.config import settings
from radio_listener.coordinator import RadioListenerCoordinator, CLIPS_DIR
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
    seconds_since_audio = round(time.time() - coordinator.last_audio_at, 1)
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
        "discord_enabled": settings.DISCORD_ENABLED and bool(coordinator.discord_notifier.webhook_url),
        "whisper_loaded": coordinator.stt.model_loaded,
        "whisper_model": coordinator.stt.model_size,
        "whisper_error": coordinator.stt.load_error,
        "audio_stream_active": coordinator.total_audio_frames_processed > 0 and seconds_since_audio < 60,
        "seconds_since_audio": seconds_since_audio,
        "audio_pipeline_crashes": coordinator.audio_pipeline_crashes,
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

@app.post("/api/alerts/dismiss")
async def dismiss_alerts():
    # Mark all active phone call alerts as not actionable now
    for alert in coordinator.alerts:
        if alert.action_type == ActionType.PHONE_CALL:
            alert.is_actionable_now = False
    coordinator._broadcast("ALERTS_DISMISSED", {})
    return {"success": True, "message": "Active call alerts dismissed"}



@app.get("/api/guesses")
async def get_guesses():
    return list(coordinator.caller_guesses)

@app.get("/api/transcripts")
async def get_transcripts():
    return list(coordinator.transcripts)

@app.post("/api/scrape_now")
async def scrape_now():
    clues = await coordinator.scraper.fetch_current_clues()
    new_found = 0
    for c in clues:
        if coordinator._merge_web_clue(c):
            new_found += 1
            coordinator._broadcast("NEW_CLUE", c.model_dump())
    scraper = coordinator.scraper
    return {
        "success": scraper.last_error is None,
        "error": scraper.last_error,
        "clues_on_page": len(clues),
        "clues_count": len(coordinator.clues),
        "new_clues": new_found,
        "last_success_at": scraper.last_success_at.isoformat() if scraper.last_success_at else None,
    }

@app.delete("/api/clues/{clue_number}")
async def delete_clue(clue_number: int):
    if clue_number in coordinator.clues:
        del coordinator.clues[clue_number]
        cap = coordinator._clue_capture
        if cap and cap["number"] == clue_number:
            coordinator._clue_capture = None
        coordinator._broadcast("CLUES_UPDATED", {})
        return {"success": True, "message": f"Clue #{clue_number} removed"}
    return {"success": False, "message": "Clue not found"}

class AddCluePayload(BaseModel):
    clue_number: int
    text: str

@app.post("/api/clues/manual")
async def add_clue_manual(payload: AddCluePayload):
    clue = ContestClue(
        clue_number=payload.clue_number,
        text=payload.text.strip(),
        source="manual_entry"
    )
    coordinator.clues[payload.clue_number] = clue
    coordinator._broadcast("NEW_CLUE", clue.model_dump())
    
    # Broadcast alert as well so history and push notifications capture it
    alert = ContestAlert(
        id=str(uuid.uuid4()),
        event_type=EventType.NEW_CLUE,
        action_type=ActionType.NONE,
        title=f"🕵️ THE ZONE: CLUE #{clue.clue_number} ADDED!",
        summary=clue.text,
        is_actionable_now=True,
        clue_number=clue.clue_number,
        clue_text=clue.text,
        confidence=1.0
    )
    coordinator.alerts.appendleft(alert)
    coordinator._broadcast("NEW_ALERT", alert.model_dump())
    await coordinator.discord_notifier.send_alert(alert)
    await coordinator.pushover_notifier.send_alert(alert)
    return {"success": True, "clue": clue, "alert": alert}

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
            "We have a brand new clue for The Secret! Clue number seven: "
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

@app.get("/api/stream.m3u8")
async def stream_m3u8_proxy():
    """
    Proxies the live master m3u8 playlist to prevent CORS and redirect issues in browsers.
    """
    url = "https://stream.jpbgdigital.com/CJZN.m3u8?device_id=dashboard-player&player_type=web"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=headers)
            return Response(
                content=resp.text,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache, no-store, must-revalidate"
                }
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch upstream stream: {e}")

# Serve dashboard
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

CLIPS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")

@app.get("/")
async def root():
    if INDEX_HTML.exists():
        return FileResponse(str(INDEX_HTML))
    return {"message": "The Zone @ 91-3 Radio Contest Listener API running"}
