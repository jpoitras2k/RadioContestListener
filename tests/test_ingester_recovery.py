import asyncio

import pytest

import radio_listener.audio.ingester as ing_mod
from radio_listener.audio.ingester import AudioStreamIngester


@pytest.mark.asyncio
async def test_ingester_survives_ffmpeg_exiting(monkeypatch):
    """ffmpeg exiting on its own used to raise ProcessLookupError in cleanup and end ingest for good."""
    ingester = AudioStreamIngester(source="stream", stream_url="unused")
    frame_bytes = ingester.frame_bytes
    # Emits two frames then exits cleanly, like ffmpeg at the end of a stream.
    monkeypatch.setattr(ingester, "_build_command", lambda: ["sh", "-c", f"head -c {frame_bytes * 2} /dev/zero"])

    real_sleep = asyncio.sleep
    monkeypatch.setattr(ing_mod.asyncio, "sleep", lambda s: real_sleep(0.01))

    frames = 0
    gen = ingester.start()

    async def consume():
        nonlocal frames
        async for _ in gen:
            frames += 1
            if frames >= 6:
                break

    await asyncio.wait_for(consume(), timeout=10)
    ingester.stop()
    assert frames >= 6
