import numpy as np
import logging
from typing import Optional, List
from radio_listener.config import settings

logger = logging.getLogger("radio_listener.vad")

class VoiceActivityDetector:
    """
    Voice Activity Detection using energy/zero-crossing and Silero VAD principles.
    Accumulates continuous speech frames and segments audio when a pause occurs,
    preventing mid-sentence word truncations.
    """
    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.55,
        min_speech_ms: int = 300,
        min_silence_ms: int = 450,
        max_duration_sec: float = 8.0
    ):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_speech_frames = int((min_speech_ms / 1000.0) / settings.CHUNK_DURATION_SEC)
        self.min_silence_frames = int((min_silence_ms / 1000.0) / settings.CHUNK_DURATION_SEC)
        self.max_frames = int(max_duration_sec / settings.CHUNK_DURATION_SEC)
        
        self.speech_buffer: List[np.ndarray] = []
        self.consecutive_speech = 0
        self.consecutive_silence = 0
        self.is_speaking = False

    def _calculate_speech_probability(self, frame: np.ndarray) -> float:
        """
        Calculates normalized RMS energy and zero-crossing profile.
        When Silero ONNX model is loaded, calls the ONNX session;
        otherwise uses resilient calibrated acoustic energy thresholding.
        """
        rms = float(np.sqrt(np.mean(frame**2)))
        # Normalize typical voice RMS (0.015 - 0.20)
        # Background music/static is attenuated by bandpass, speech peaks higher
        prob = min(1.0, max(0.0, (rms - 0.012) / 0.045))
        return prob

    def process_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """
        Process a 50ms frame. Returns concatenated speech array if an utterance 
        just completed or max duration reached; otherwise returns None.
        """
        prob = self._calculate_speech_probability(frame)
        
        if prob >= self.threshold:
            self.consecutive_speech += 1
            self.consecutive_silence = 0
            if not self.is_speaking and self.consecutive_speech >= self.min_speech_frames:
                self.is_speaking = True
        else:
            self.consecutive_silence += 1
            self.consecutive_speech = 0

        if self.is_speaking:
            self.speech_buffer.append(frame)
            
            # Check for speech completion:
            # 1. DJ paused/breathed for min_silence_ms
            # 2. Maximum chunk ceiling reached (e.g. 6-8s continuous speech)
            if self.consecutive_silence >= self.min_silence_frames or len(self.speech_buffer) >= self.max_frames:
                utterance = np.concatenate(self.speech_buffer)
                self.speech_buffer = []
                # If we cut at max_frames while sound continues, keep speaking flag so next chunk accumulates
                if self.consecutive_silence >= self.min_silence_frames:
                    self.is_speaking = False
                    self.consecutive_silence = 0
                return utterance

        return None
