import asyncio
import logging
from typing import AsyncGenerator, Optional
import numpy as np
from radio_listener.config import settings

logger = logging.getLogger("radio_listener.audio")

class AudioStreamIngester:
    """
    Ingests live audio from either:
    1. Over-the-air RTL-SDR USB dongle (rtl_fm)
    2. Online stream via FFmpeg (HLS/Icecast)
    3. Mock audio generator for simulations
    Outputs standardized 16kHz 16-bit mono PCM chunks.
    """
    def __init__(
        self, 
        source: Optional[str] = None, 
        stream_url: Optional[str] = None, 
        freq_mhz: Optional[float] = None
    ):
        self.source = source or settings.AUDIO_SOURCE
        self.stream_url = stream_url or settings.STATION_STREAM_URL
        self.freq_mhz = freq_mhz or settings.STATION_FREQUENCY_MHZ
        self.process: Optional[asyncio.subprocess.Process] = None
        self._running = False
        self.bytes_per_sample = 2 # 16-bit
        self.sample_rate = settings.SAMPLE_RATE
        # Frame size in bytes (e.g. 50ms at 16kHz = 800 samples = 1600 bytes)
        self.frame_samples = int(self.sample_rate * settings.CHUNK_DURATION_SEC)
        self.frame_bytes = self.frame_samples * self.bytes_per_sample

    def _build_command(self) -> list[str]:
        if self.source == "sdr":
            # Direct wideband FM demodulation via RTL-SDR with de-emphasis
            return [
                "rtl_fm",
                "-f", f"{self.freq_mhz}M",
                "-M", "wfm",
                "-s", "200k",
                "-r", "16k",
                "-E", "deemp",
                "-g", "32"
            ]
        elif self.source == "stream":
            # -reconnect_at_eof/-reconnect_streamed must not be used here: with HLS they make
            # ffmpeg re-read the master playlist endlessly and it never finds an audio stream.
            # Reconnects are handled by the restart loop in start().
            return [
                "ffmpeg",
                "-nostdin",
                "-loglevel", "error",
                "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "-i", self.stream_url,
                "-af", "highpass=f=200,lowpass=f=3500,volume=1.2",
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ac", "1",
                "-ar", str(self.sample_rate),
                "pipe:1"
            ]
        elif self.source == "mock":
            # Mock source for testing
            return []
        else:
            raise ValueError(f"Unknown audio source: {self.source}")

    async def start(self) -> AsyncGenerator[np.ndarray, None]:
        self._running = True
        logger.info(f"Starting audio ingest: source={self.source}")
        
        while self._running:
            if self.source == "mock":
                # Generate synthetic low-level noise when in mock mode
                await asyncio.sleep(settings.CHUNK_DURATION_SEC)
                synthetic_pcm = np.random.normal(0, 0.01, self.frame_samples).astype(np.float32)
                yield synthetic_pcm
                continue

            cmd = self._build_command()
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=None
                )
                logger.info(f"Subprocess spawned: {' '.join(cmd)}")
                
                while self._running and self.process.returncode is None:
                    raw_bytes = await asyncio.wait_for(
                        self.process.stdout.readexactly(self.frame_bytes), timeout=30.0
                    )
                    if not raw_bytes:
                        break
                    
                    # Convert raw 16-bit PCM to normalized float32 [-1.0, 1.0]
                    pcm_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
                    pcm_float32 = pcm_int16.astype(np.float32) / 32768.0
                    yield pcm_float32
                    
            except asyncio.IncompleteReadError:
                logger.warning("Stream ended or interrupted. Reconnecting in 2 seconds...")
            except asyncio.TimeoutError:
                logger.warning("No audio received for 30s. Restarting stream...")
            except Exception as e:
                logger.error(f"Error in audio ingest: {e}")
            finally:
                await self._stop_process()

            if self._running:
                await asyncio.sleep(2.0)

    async def _stop_process(self):
        # terminate()/kill() raise ProcessLookupError once ffmpeg has already exited, which
        # previously escaped this cleanup and silently ended the whole audio pipeline.
        proc, self.process = self.process, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    def kill_current_process(self):
        """Forces the current ffmpeg to exit so start() reconnects (used by the stall watchdog)."""
        proc = self.process
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    def stop(self):
        self._running = False
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
