import logging
import numpy as np
from typing import Optional, List
from radio_listener.config import settings

logger = logging.getLogger("radio_listener.stt")

# Station-specific vocabulary biasing to minimize WER on numbers, DJ names, and contest terms
ZONE_INITIAL_PROMPT = (
    "The Zone @ 91-3, CJZN, Victoria, Pol, Jenny, Jason Lamb, Kirsten, "
    "The Secret, shhhhhh, secret clue, clue number, riddle, "
    "Vancouver Island, premier, David Eby, Victoria, 250-475-0913, 1-888-414-0913."
)

class WhisperTranscriber:
    """
    faster-whisper inference engine with station vocabulary prompting.
    Gracefully falls back to mock transcriber if model weights or GPU are unavailable.
    """
    def __init__(
        self,
        model_size: str = settings.WHISPER_MODEL_SIZE,
        device: str = settings.WHISPER_DEVICE,
        compute_type: str = settings.WHISPER_COMPUTE_TYPE
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model = None
        self.model_loaded = False
        self.load_error = None
        self._load_model()

    def _load_model(self):
        try:
            from faster_whisper import WhisperModel
            import torch
            dev = "cuda" if (self.device == "cuda" or (self.device == "auto" and torch.cuda.is_available())) else "cpu"
            c_type = "float16" if dev == "cuda" else "int8"
            logger.info(f"Loading faster-whisper model '{self.model_size}' on {dev} ({c_type})...")
            # We initialize lazily or load small if on CPU
            self.model = WhisperModel(self.model_size, device=dev, compute_type=c_type)
            self.model_loaded = True
            logger.info("faster-whisper loaded successfully.")
        except Exception as e:
            self.load_error = str(e)
            logger.warning(f"Could not load faster-whisper ({e}). Transcriber running in simulation/mock fallback.")
            self.model = None

    def transcribe(self, audio_data: np.ndarray) -> str:
        """
        Transcribe audio slice (float32, 16kHz mono).
        """
        if self.model is None:
            # Fallback when running without downloaded weights or GPU
            return ""

        try:
            segments, info = self.model.transcribe(
                audio_data,
                initial_prompt=ZONE_INITIAL_PROMPT,
                condition_on_previous_text=False,
                no_speech_threshold=0.8,
                vad_filter=True,
                language="en"
            )
            text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])
            return text
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            return ""
