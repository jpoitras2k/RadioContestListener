import asyncio
import logging
import time
import uuid
from collections import deque
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
            
        self._tasks.append(asyncio.create_task(self._run_audio_pipeline()))
        self._tasks.append(asyncio.create_task(self._run_web_watcher()))

    async def stop(self):
        self.is_running = False
        self.ingester.stop()
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        logger.info("RadioListenerCoordinator stopped.")

    async def _run_web_watcher(self):
        """Polls /shhhhhh/ contest webpage every 60 seconds."""
        while self.is_running:
            try:
                clues = await self.scraper.fetch_current_clues()
                for c in clues:
                    if c.clue_number not in self.clues:
                        logger.info(f"New clue discovered on website! Clue #{c.clue_number}")
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
                        await self.pushover_notifier.send_alert(alert)
                        await self.discord_notifier.send_alert(alert)
                    else:
                        self.clues[c.clue_number] = c
            except Exception as e:
                logger.error(f"Error in web watcher loop: {e}")
            await asyncio.sleep(60.0)

    async def _run_audio_pipeline(self):
        """Consumes PCM audio stream, runs VAD, STT, and detection."""
        async for frame in self.ingester.start():
            if not self.is_running:
                break
            self.total_audio_frames_processed += 1
            
            # Check if an utterance finished
            utterance = self.vad.process_frame(frame)
            if utterance is not None:
                self.total_speech_segments += 1
                text = self.stt.transcribe(utterance)
                if text:
                    await self.process_transcript_text(text)

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

        # Record new clue if applicable
        if analysis.event_type == EventType.NEW_CLUE and analysis.clue_number:
            new_clue = ContestClue(
                clue_number=analysis.clue_number,
                text=analysis.clue_text or text,
                source="radio_stt"
            )
            self.clues[analysis.clue_number] = new_clue
            self._broadcast("NEW_CLUE", new_clue.model_dump())

        self._broadcast("NEW_ALERT", alert.model_dump())

        # Dispatch immediate notifications (Pushover & Discord)
        await self.pushover_notifier.send_alert(alert)
        await self.discord_notifier.send_alert(alert)
        return alert
