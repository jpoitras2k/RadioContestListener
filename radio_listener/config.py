import os
from typing import List, Optional
from pydantic import BaseModel, Field

class Settings(BaseModel):
    # Radio & Audio configuration
    STATION_NAME: str = "The Zone @ 91-3"
    STATION_CALLSIGN: str = "CJZN-FM"
    STATION_FREQUENCY_MHZ: float = 91.3
    STATION_STUDIO_PHONE: str = "250-475-0913"
    STATION_STUDIO_PHONE_CLEAN: str = "2504750913"
    STATION_TOLLFREE_PHONE: str = "1-888-414-0913"
    STATION_STREAM_URL: str = os.getenv(
        "STATION_STREAM_URL", 
        "https://redirect.jpbgdigital.com/CJZN.m3u8"
    )
    CONTEST_URL: str = os.getenv(
        "CONTEST_URL", 
        "https://www.thezone.fm/shhhhhh/"
    )
    
    # Audio capture settings
    AUDIO_SOURCE: str = os.getenv("AUDIO_SOURCE", "stream") # "stream" | "sdr" | "mock"
    SAMPLE_RATE: int = 16000
    CHANNELS: int = 1
    CHUNK_DURATION_SEC: float = 0.05 # 50ms frames for VAD
    
    # VAD settings
    VAD_THRESHOLD: float = 0.55
    MIN_SPEECH_DURATION_MS: int = 300
    MIN_SILENCE_DURATION_MS: int = 450
    SPEECH_PAD_MS: int = 150
    MAX_CHUNK_DURATION_SEC: float = 8.0
    
    # STT settings
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "distil-large-v3")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "auto") # "cuda" | "cpu" | "auto"
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "default") # "float16" | "int8" | "default"
    
    # Ollama settings
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    OLLAMA_TIMEOUT_SEC: float = 4.0
    
    # Pushover settings
    PUSHOVER_ENABLED: bool = os.getenv("PUSHOVER_ENABLED", "false").lower() == "true"
    PUSHOVER_APP_TOKEN: str = os.getenv("PUSHOVER_APP_TOKEN", "")
    PUSHOVER_USER_KEY: str = os.getenv("PUSHOVER_USER_KEY", "")
    
    # Web dashboard & API
    PORT: int = int(os.getenv("PORT", "4173"))
    HOST: str = "0.0.0.0"

settings = Settings()
