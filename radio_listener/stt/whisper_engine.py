import logging
import numpy as np
from typing import Optional, List
from radio_listener.config import settings

logger = logging.getLogger("radio_listener.stt")

# Station-specific vocabulary biasing to minimize WER on numbers, DJ names, and contest terms
ZONE_INITIAL_PROMPT = (
    "The Zone @ 91-3, CJZN, Victoria, Pol, Jenny, Jason Lamb, Kirsten, "
    "The Secret, shhhhhh, secret clue, clue number, riddle, GI Snagglepuss, "
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
            import ctranslate2
            has_cuda = ctranslate2.get_cuda_device_count() > 0
            dev = "cuda" if (self.device == "cuda" or (self.device == "auto" and has_cuda)) else "cpu"
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

    def transcribe_clip(self, audio_data: np.ndarray, window_sec: float = 15.0) -> str:
        """
        Slower, thorough pass over a saved clue clip. Clues are often read over a music bed,
        which the live settings (VAD + no-speech threshold) discard, so those filters are off
        here and the clip is decoded in short windows to stop Whisper skipping long stretches.
        """
        if self.model is None:
            return ""
        model = self.model
        clip_model_name = settings.WHISPER_CLIP_MODEL_SIZE
        loaded_extra = False
        if clip_model_name and clip_model_name != self.model_size:
            # Loaded only for this pass and released afterwards so small droplets can afford it.
            try:
                from faster_whisper import WhisperModel
                model = WhisperModel(clip_model_name, device="cpu", compute_type="int8", cpu_threads=1)
                loaded_extra = True
            except Exception as e:
                logger.warning(f"Could not load clip model '{clip_model_name}' ({e}); using live model.")
        try:
            return self._transcribe_windows(model, audio_data, window_sec)
        finally:
            if loaded_extra:
                del model
                import gc
                gc.collect()

    def _transcribe_windows(self, model, audio_data: np.ndarray, window_sec: float) -> str:
        # Speech over a music bed decodes very differently depending on where a window starts,
        # so windows overlap by half and every window's text is kept. Some lines repeat, but a
        # riddle that one alignment drops is usually caught by the neighbouring one.
        # No initial prompt: in testing it made Whisper skip sung/backed speech more often.
        sr = settings.SAMPLE_RATE
        total = len(audio_data) / sr
        step_sec = window_sec / 2
        lines = []
        start_sec = 0.0
        while start_sec < total:
            chunk = audio_data[int(start_sec * sr):int((start_sec + window_sec) * sr)]
            if len(chunk) < sr:
                break
            try:
                segments, _ = model.transcribe(
                    chunk,
                    condition_on_previous_text=False,
                    no_speech_threshold=None,
                    log_prob_threshold=None,
                    vad_filter=False,
                    beam_size=5,
                    language="en",
                )
                text = " ".join(s.text.strip() for s in segments if s.text.strip())
            except Exception as e:
                logger.error(f"Whisper clip transcription error: {e}")
                text = ""
            if text:
                t = int(start_sec)
                lines.append(f"[{t // 60}:{t % 60:02d}] {text}")
            if start_sec + window_sec >= total:
                break
            start_sec += step_sec
        return "\n".join(lines)
