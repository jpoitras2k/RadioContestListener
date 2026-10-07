import asyncio
import logging
import time
import uuid
import wave
from collections import deque
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Callable, Dict, Any
import numpy as np

from radio_listener.config import settings
from radio_listener.models import (
    TranscriptSegment, ContestAlert, ContestClue, CallerGuess,
    EventType, ActionType, Verdict, AnalysisResult
)
from radio_listener.audio.ingester import AudioStreamIngester
from radio_listener.vad.silero_gate import VoiceActivityDetector
from radio_listener.stt.whisper_engine import WhisperTranscriber
from radio_listener.detector.regex_gate import FastRegexGate
from radio_listener.detector.ollama_client import OllamaReasoner
from radio_listener.notifier.pushover import PushoverNotifier
from radio_listener.notifier.discord import DiscordNotifier
from radio_listener.scraper.web_watcher import ContestWebScraper

logger = logging.getLogger("radio_listener.coordinator")

CLIPS_DIR = Path(__file__).resolve().parent.parent / "clips"
CLUE_PRE_ROLL_SEC = 60.0
CLUE_CAPTURE_SEC = 60.0
# Whisper runs behind live audio, so keep accepting transcript text a little longer.
CLUE_TEXT_GRACE_SEC = 30.0
# Repeated mentions within this window extend the same on-air clue instead of creating another.
CLUE_MERGE_WINDOW_SEC = 15 * 60

class RadioListenerCoordinator:
    """
    Central orchestration engine for the radio listener.
    Coordinates audio ingestion, VAD gating, speech-to-text, 
    rolling 45-second context maintenance, two-tier detection, 
    notification routing, and web clue tracking.
    """
    def __init__(self):
        self.ingester = AudioStreamIngester()
        self.vad = VoiceActivityDetector()
        self.stt = WhisperTranscriber()
        self.regex_gate = FastRegexGate()
        self.reasoner = OllamaReasoner()
        self.pushover_notifier = PushoverNotifier()
        self.discord_notifier = DiscordNotifier()
        self.scraper = ContestWebScraper()
        
        # State stores
        self.transcripts: deque[TranscriptSegment] = deque(maxlen=200)
        self.alerts: deque[ContestAlert] = deque(maxlen=100)
        self.clues: dict[int, ContestClue] = {}
        self.caller_guesses: deque[CallerGuess] = deque(maxlen=100)
        
        # Sliding transcript window (holds segments spanning the last 45 seconds)
        self._context_window: deque[TranscriptSegment] = deque()
        self.is_running = False
        self._tasks: List[asyncio.Task] = []
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []
        self.total_audio_frames_processed = 0
        self.total_speech_segments = 0
        self.total_triggers_fired = 0

        frames_per_sec = int(round(1.0 / settings.CHUNK_DURATION_SEC))
        self._audio_ring: deque[np.ndarray] = deque(maxlen=int(CLUE_PRE_ROLL_SEC * frames_per_sec))
        self._clue_capture: Optional[Dict[str, Any]] = None

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]):
        self._subscribers.append(callback)

    def _broadcast(self, event_type: str, data: Any):
        payload = {"type": event_type, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}
        for cb in self._subscribers:
            try:
                cb(payload)
            except Exception as e:
                logger.error(f"Error in subscriber callback: {e}")

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        logger.info("Starting RadioListenerCoordinator...")
        
        # Initial seed of clues from contest webpage
        initial_clues = await self.scraper.fetch_current_clues()
        for c in initial_clues:
            self.clues[c.clue_number] = c
            
        self._utterance_queue = asyncio.Queue(maxsize=8)
        self._tasks.append(asyncio.create_task(self._run_audio_pipeline()))
        self._tasks.append(asyncio.create_task(self._run_transcriber()))
        self._tasks.append(asyncio.create_task(self._run_web_watcher()))

    async def stop(self):
        self.is_running = False
        self.ingester.stop()
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        logger.info("RadioListenerCoordinator stopped.")

    async def _run_web_watcher(self):
        """Polls /shhhhhh/ contest webpage every 30 seconds for newly posted clues."""
        while self.is_running:
            try:
                clues = await self.scraper.fetch_current_clues()
                for c in clues:
                    existing = self.clues.get(c.clue_number)
                    if existing is None or existing.source == "radio_stt":
                        logger.info(f"New clue discovered on website! Clue #{c.clue_number}")
                        if existing is not None:
                            c.audio_url = existing.audio_url
                        self.clues[c.clue_number] = c
                        alert = ContestAlert(
                            id=str(uuid.uuid4()),
                            event_type=EventType.NEW_CLUE,
                            action_type=ActionType.NONE,
                            title=f"🕵️ THE ZONE: CLUE #{c.clue_number} POSTED!",
                            summary=c.text,
                            is_actionable_now=True,
                            clue_number=c.clue_number,
                            clue_text=c.text,
                            confidence=1.0
                        )
                        self.alerts.appendleft(alert)
                        self._broadcast("NEW_ALERT", alert.model_dump())
                        self._broadcast("NEW_CLUE", c.model_dump())
                        await self.pushover_notifier.send_alert(alert)
                        await self.discord_notifier.send_alert(alert)
                    else:
                        c.audio_url = existing.audio_url
                        self.clues[c.clue_number] = c
            except Exception as e:
                logger.error(f"Error in web watcher loop: {e}")
            await asyncio.sleep(30.0)

    async def _run_audio_pipeline(self):
        """Consumes PCM audio stream, runs VAD, STT, and detection."""
        async for frame in self.ingester.start():
            if not self.is_running:
                break
            self.total_audio_frames_processed += 1
            self._audio_ring.append(frame)
            cap = self._clue_capture
            if cap and cap["recording"]:
                cap["frames"].append(frame)
            
            # Check if an utterance finished
            utterance = self.vad.process_frame(frame)
            if utterance is not None:
                self.total_speech_segments += 1
                # If Whisper can't keep up with the live stream, drop the oldest chunk
                # rather than letting audio reading fall behind.
                if self._utterance_queue.full():
                    self._utterance_queue.get_nowait()
                    logger.warning("Transcriber falling behind; dropped oldest speech chunk.")
                self._utterance_queue.put_nowait(utterance)

    async def _run_transcriber(self):
        """Runs Whisper off the event loop so audio ingest and the API stay responsive."""
        while self.is_running:
            utterance = await self._utterance_queue.get()
            try:
                text = await asyncio.to_thread(self.stt.transcribe, utterance)
                if text and text.strip():
                    logger.info(f"Speech transcribed: '{text}'")
                    await self.process_transcript_text(text)
            except Exception as e:
                logger.error(f"Error transcribing speech chunk: {e}")

    def _next_clue_number(self) -> int:
        confirmed = [n for n, c in self.clues.items() if c.source != "radio_stt"]
        return (max(confirmed) if confirmed else 0) + 1

    def _start_clue_capture(self, spoken_number: Optional[int], context_str: str) -> tuple[int, bool]:
        """Begins (or extends) recording of a clue being read on air. Returns (clue_number, is_new)."""
        now = time.time()
        number = spoken_number or self._next_clue_number()
        cap = self._clue_capture
        if cap and now - cap["started"] < CLUE_MERGE_WINDOW_SEC and (spoken_number is None or spoken_number == cap["number"]):
            cap["text_until"] = max(cap["text_until"], now + CLUE_CAPTURE_SEC + CLUE_TEXT_GRACE_SEC)
            if not cap["recording"]:
                self._begin_audio_recording(cap)
            return cap["number"], False

        if cap and cap["recording"]:
            self._finish_audio_recording(cap)

        cap = {
            "number": number,
            "started": now,
            "texts": [context_str] if context_str else [],
            "text_until": now + CLUE_CAPTURE_SEC + CLUE_TEXT_GRACE_SEC,
            "recording": False,
            "frames": [],
            "clip_index": 0,
        }
        self._clue_capture = cap
        self._begin_audio_recording(cap)
        self._save_captured_clue(cap)
        return number, True

    def _begin_audio_recording(self, cap: Dict[str, Any]):
        cap["frames"] = list(self._audio_ring)
        cap["recording"] = True
        cap["clip_index"] += 1
        clip_index = cap["clip_index"]

        async def stop_later():
            await asyncio.sleep(CLUE_CAPTURE_SEC)
            if self._clue_capture is cap and cap["recording"] and cap["clip_index"] == clip_index:
                self._finish_audio_recording(cap)

        try:
            asyncio.get_running_loop().create_task(stop_later())
        except RuntimeError:
            pass

    def _finish_audio_recording(self, cap: Dict[str, Any]):
        cap["recording"] = False
        frames, cap["frames"] = cap["frames"], []
        if not frames:
            return
        try:
            CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(cap["started"], timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            name = f"clue-{cap['number']}-{stamp}-{cap['clip_index']}.wav"
            pcm = (np.clip(np.concatenate(frames), -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(CLIPS_DIR / name), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(settings.SAMPLE_RATE)
                wf.writeframes(pcm.tobytes())
            cap["audio_url"] = f"/clips/{name}"
            logger.info(f"Saved on-air clue audio: {name}")
            self._save_captured_clue(cap)
        except Exception as e:
            logger.error(f"Failed to save clue audio clip: {e}")

    def _append_to_clue_capture(self, text: str):
        cap = self._clue_capture
        if not cap or time.time() > cap["text_until"]:
            return
        # The first entry is the pre-trigger context window, which already contains recent lines.
        if cap["texts"] and text in cap["texts"][0] and len(cap["texts"]) == 1:
            return
        cap["texts"].append(text)
        self._save_captured_clue(cap)

    def _save_captured_clue(self, cap: Dict[str, Any]):
        existing = self.clues.get(cap["number"])
        if existing is not None and existing.source != "radio_stt":
            # The website (or a manual entry) already has this clue; just attach the recording.
            if cap.get("audio_url"):
                existing.audio_url = cap["audio_url"]
                self._broadcast("NEW_CLUE", existing.model_dump())
            return
        clue = ContestClue(
            clue_number=cap["number"],
            text=" ".join(cap["texts"]).strip() or "(waiting for transcript…)",
            source="radio_stt",
            status="unconfirmed",
            audio_url=cap.get("audio_url"),
            discovered_at=datetime.fromtimestamp(cap["started"], timezone.utc),
        )
        self.clues[cap["number"]] = clue
        self._broadcast("NEW_CLUE", clue.model_dump())

    async def process_transcript_text(self, text: str, custom_timestamp: Optional[datetime] = None) -> Optional[ContestAlert]:
        """
        Processes a newly transcribed utterance through the two-tier detection pipeline.
        Can also be invoked directly by test harnesses and UI simulators.
        """
        now = custom_timestamp or datetime.now(timezone.utc)
        now_ts = time.time()
        
        # Tier-1 fast regex evaluation
        match = self.regex_gate.evaluate(text)
        is_trigger = match is not None

        seg = TranscriptSegment(
            text=text,
            start_time=now_ts - 3.0,
            end_time=now_ts,
            confidence=0.9,
            timestamp=now,
            is_trigger=is_trigger
        )
        self.transcripts.appendleft(seg)
        self._context_window.append(seg)
        
        # Prune context window to last 45 seconds
        while self._context_window and (now_ts - self._context_window[0].end_time > 45.0):
            self._context_window.popleft()

        self._broadcast("NEW_TRANSCRIPT", seg.model_dump())
        self._append_to_clue_capture(text)

        if not is_trigger:
            return None

        self.total_triggers_fired += 1
        logger.info(f"Tier-1 match triggered on text: '{text}' (Rule: {match['rule_name']})")

        # Compile rolling 45s context transcript
        context_str = " ".join([s.text for s in self._context_window])

        # Tier-2 LLM Reasoning
        analysis: AnalysisResult = await self.reasoner.analyze_context(context_str)
        
        if analysis.event_type == EventType.IRRELEVANT:
            logger.info(f"Ollama determined event is IRRELEVANT ({analysis.summary})")
            return None

        # Build Alert
        alert_id = str(uuid.uuid4())
        title = f"📻 THE ZONE 91-3: {analysis.event_type.value}"
        if analysis.event_type == EventType.CUE_TO_CALL:
            title = "🚨 THE ZONE 91-3: LINES OPEN! CALL NOW!"
        elif analysis.event_type == EventType.NEW_CLUE:
            title = f"🕵️ THE ZONE: CLUE #{analysis.clue_number or 'NEW'} ANNOUNCED!"
        elif analysis.event_type == EventType.CALLER_GUESS:
            title = f"❌ The Secret: Guess '{analysis.caller_guess_location or 'Unknown'}'"

        alert = ContestAlert(
            id=alert_id,
            event_type=analysis.event_type,
            action_type=analysis.action_type,
            title=title,
            summary=analysis.summary or text,
            is_actionable_now=analysis.is_actionable_now,
            phone_number=analysis.phone_number,
            dial_url=analysis.dial_url,
            sms_url=analysis.sms_url,
            clue_number=analysis.clue_number,
            clue_text=analysis.clue_text,
            caller_guess=analysis.caller_guess_location,
            verdict=analysis.dj_verdict,
            confidence=analysis.confidence,
            raw_transcript_window=context_str
        )

        self.alerts.appendleft(alert)

        # Record caller guess if applicable
        if analysis.event_type == EventType.CALLER_GUESS and analysis.caller_guess_location:
            guess = CallerGuess(
                location_or_answer=analysis.caller_guess_location,
                verdict=analysis.dj_verdict or Verdict.INCORRECT,
                notes=analysis.dj_commentary or analysis.summary
            )
            self.caller_guesses.appendleft(guess)
            self._broadcast("NEW_GUESS", guess.model_dump())

        if analysis.event_type == EventType.NEW_CLUE:
            clue_number, is_new = self._start_clue_capture(analysis.clue_number, context_str)
            if not is_new:
                self.alerts.popleft()
                logger.info(f"Clue #{clue_number} mentioned again; extending existing capture.")
                return None
            alert.clue_number = clue_number
            alert.title = f"🕵️ THE ZONE: CLUE #{clue_number} ON AIR NOW!"
            alert.summary = (
                f"A Secret clue is being read on air. Saving it as Clue #{clue_number} "
                f"(unconfirmed) with a 2-minute audio clip. {analysis.summary}"
            )

        self._broadcast("NEW_ALERT", alert.model_dump())

        # Dispatch immediate notifications (Pushover & Discord)
        await self.pushover_notifier.send_alert(alert)
        await self.discord_notifier.send_alert(alert)
        return alert
