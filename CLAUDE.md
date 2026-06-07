# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI-powered video subtitle & audio track manager. Core workflow: extract audio → ASR → generate subtitles; extract text → TTS → generate dubbing; remux with FFmpeg.

**Language:** Python 3.14
**FFmpeg:** 8.1.1

## Key libraries

| Library | Purpose |
|----------|---------|
| `fastapi` | Web framework (REST API + Jinja2 templates) |
| `uvicorn` | ASGI server |
| `httpx` | HTTP client (GPU Worker calls) |
| `faster-whisper` | Local ASR (Whisper via CTranslate2, 4x faster than openai-whisper) |
| `ffmpeg-python` | FFmpeg bindings |
| `pydub` | Audio processing |
| `pysrt` | SRT subtitle parsing |
| `silero-vad` | Voice activity detection (reduces Whisper hallucinations on silence) |
| `psutil` | Hardware detection (CPU/RAM/Disk) |
| `python-dotenv` | `.env` file loading |

## Architecture

Layered design (see `docs/00-overview.md` for data flow diagrams):

```
api/          → FastAPI REST endpoints (thin layer over core/ + asr/ + tts/ + translate/)
templates/    → Jinja2 + HTMX server-rendered pages
pipeline/     → End-to-end workflows (ASR→subtitles→mux, ASR→translate→TTS→mux, track switching)
core/         → FFmpeg operations (probe, extract, mux, burn, sync)
asr/          → Speech recognition engines (engine.py abstract base + whisper_local / whisper_api)
tts/          → TTS engines (engine.py abstract base + edge_tts / xtts) + audio alignment (align.py)
translate/    → Translation engines (engine.py abstract base + llm / llm_local / deepl)
subtitle/     → Subtitle format handling (srt, ass, convert)
config/       → Two-layer config: settings.yaml (committed) + settings.local.yaml (gitignored)
              → requirements.py: hardware detection + 5-tier profile matching + minimum requirements check
```

**Deployment** (default: Docker, also supports direct run):
- Docker: `docker compose up -d` → FastAPI on :8000
- Direct: `python app.py` (requires Python 3.13+ + FFmpeg)
- GPU Worker: separate `worker.py` on host machine (:9001) for CUDA inference

Each AI module (`asr/`, `tts/`, `translate/`) follows a strategy pattern: `engine.py` defines an abstract interface, and implementations are swappable.

## FFmpeg notes

- Preferred output container: **MKV** (supports soft subtitles + multi-audio natively)
- MP4 only accepts `mov_text` subtitles; use `-c copy` to avoid re-encoding when the container supports the codec
- ISO 639-2 language codes on tracks: `-metadata:s:s:0 language=eng`
- Stream disposition controls default track: `-disposition:s:0 default`

## Development phases

1. **Phase 1 (MVP):** FFprobe stream detection → add external SRT as soft sub → switch default subtitle track
2. **Phase 2:** Subtitle format conversion (SRT↔ASS↔WebVTT), audio track add/switch, hard-sub burn-in
3. **Phase 3:** Whisper ASR integration, LLM translation, TTS dubbing with audio alignment

## Model

`faster-whisper` model files download automatically on first run to `HF_HOME` (see `config/settings.yaml` paths). The active model is determined by the hardware profile selected at startup (see `config/requirements.py`): 5 tiers from `cpu` (tiny) to `gpu_ultra` (large-v3-turbo). Profile can be overridden in `config/settings.local.yaml`.

This dev machine auto-selects the profile matching its GPU VRAM (see `config/requirements.py`). Profile can be overridden in `config/settings.local.yaml`.

## Commands

```bash
# Install dependencies
python -m pip install <pkg>

# FFmpeg (ensure it's on PATH or set in config/settings.local.yaml)
ffprobe -v quiet -print_format json -show_streams input.mp4

# Run tests
python -m pytest tests/ -v
```
