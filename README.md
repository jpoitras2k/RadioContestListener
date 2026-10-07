# The Zone @ 91-3 (CJZN-FM) Contest Listener & Secret Radar

An automated listener and real-time alert daemon for Victoria, BC's modern rock radio station **The Zone @ 91-3 (CJZN-FM)**, specifically engineered to detect on-air contest cues, phone call-ins ("Caller 13"), and clues for **"The Secret"** (`/shhhhhh/`).

---

## Architecture Overview

```
                                [ Radio Source ]
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
        [ RTL-SDR 91.3 MHz ]                         [ Web HLS Stream ]
        (Sub-100ms RF Latency)                      (Auto-reconnecting FFmpeg)
                 └──────────────────────┬──────────────────────┘
                                        ▼
                           [ Ingestion: 16kHz Mono ]
                                        │
                           [ Silero VAD Speech Gating ]
                           (Eliminates static & music)
                                        │
                       [ faster-whisper (distil-large-v3) ]
                       (Station vocabulary prompt biasing)
                                        │
                                        ▼
                        [ Tier-1: Fast Regex Pre-Filter ]
                        (Sub-0.1ms execution; drops >98%)
                                        │
                                        ▼ (Matches Cues/Clues/Guesses)
                        [ Tier-2: Ollama LLM Reasoning ]
                        (Llama 3.2 3B Structured JSON)
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
       [ Pushover Mobile Push ]                     [ Real-Time Web Radar ]
    (Priority 2 Siren + Tap-to-Dial)             (Live transcript & clue board)
```

---

## Key Features

1. **Dual Ingestion Engine**:
   - **RTL-SDR FM Demodulation**: Tunes directly to `91.3 MHz` in Victoria or `97.1 MHz` in Sooke via `rtl_fm`. Bypasses the 20–35 second web/app stream buffering lag for speed contests ("Be caller 13").
   - **Stream Fallback**: Connects to the station's digital HLS stream with automatic connection recovery and 200 Hz – 3500 Hz audio bandpass filtering.
2. **Speech-Gated Transcription**:
   - Continuous VAD chunking ensures words and phone numbers are never split across arbitrary time intervals.
   - Initial prompt conditioning biases Whisper's vocabulary towards station names, DJs (*Pol*, *Jenny*, *Jason Lamb*, *Kirsten*), phone numbers (`250-475-0913`), and contest phrases.
3. **Two-Tier Detection Pipeline**:
   - **Tier-1 Regex Pre-Filter**: Discards over 98% of regular music and banter in less than 0.1ms.
   - **Tier-2 Ollama Context Reasoner**: Inspects the 45-second sliding conversation window, filtering out teasers ("coming up at 8:20") and recaps, and formatting live actionable events into structured JSON.
4. **Contest Scraper & Caller Guess Tracker**:
   - Monitors `https://www.thezone.fm/shhhhhh/` for new clue postings.
   - Automatically records caller guesses heard on-air to maintain a list of ruled-out locations.
5. **Instant Tap-to-Dial Mobile Push**:
   - Sends emergency alerts to Pushover with `priority=2` (bypassing silent/Do-Not-Disturb modes).
   - Embedded `tel:2504750913` URL lets you tap the notification banner to immediately dial the studio.

---

## Quickstart & Local Setup

### 1. Requirements
- Python 3.10+
- FFmpeg (for stream ingest)
- `rtl-sdr` (optional, for physical USB FM receiver)
- NVIDIA GPU with CUDA (optional for local Whisper & Ollama; runs CPU fallback automatically)

### 2. Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file or export environment variables:
```bash
# Ingestion: "stream" (default) or "sdr"
AUDIO_SOURCE=stream

# Pushover Mobile Push (Get keys at https://pushover.net/)
PUSHOVER_ENABLED=true
PUSHOVER_APP_TOKEN=your_app_token_here
PUSHOVER_USER_KEY=your_user_key_here

# Ollama Local LLM (Optional: defaults to http://127.0.0.1:11434)
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:3b

# Web Dashboard Port
PORT=4173
```

### 4. Running the Daemon
```bash
python3 main.py
```
Open **`http://localhost:4173`** in your browser to access the live dashboard, view active clues, inspect transcripts, and simulate test scenarios.

### 5. Running the Test Suite
```bash
pytest tests/
```
