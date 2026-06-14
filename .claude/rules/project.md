# Project Rules

## Python version

This project uses Python 3.14. Before adding a dependency, verify it has a cp314 wheel on PyPI.
Packages that only ship cp312/cp313 wheels will fail to install.

## Architecture pattern

Each AI module follows a **strategy pattern** with an abstract base:

```
engines/<name>/
├── engine.py    # Abstract base class defining the interface
├── impl_a.py    # Concrete implementation A
└── impl_b.py    # Concrete implementation B
```

When adding a new engine implementation, subclass the abstract base in `engine.py` — do not modify existing implementations to add features.

## FFmpeg conventions

- Prefer `-c copy` (stream copy) over re-encoding unless burn-in is requested
- Always use `-map` explicitly when remuxing; never rely on FFmpeg's auto stream selection
- Language codes: ISO 639-2 three-letter (`eng`, `jpn`, `chi`, `kor`, `zho`)
- Output container: MKV by default; MP4 only when the user explicitly needs it

## Git rules

- **Never** commit personal information: no absolute paths, no hardware specs, no API keys, no local URLs
- Personal config lives in `config/settings.local.yaml` and `.env` (both gitignored) — commit only `settings.yaml` and `.env.example`
- The `.claude/memory/` directory is gitignored; `MEMORY.md` (the index) may be committed but must contain only config-key references, no actual paths or hardware details
- When updating committed docs (CLAUDE.md, rules, docs/), reference config keys (e.g. `paths.model_root`), never literal paths (e.g. `D:\AI\Models`)

## Code style

- Type hints on all public function signatures
- Docstrings in Chinese (matching the project's primary language)
- Configuration via `config/settings.yaml` (committed) + `config/settings.local.yaml` (gitignored) — never hardcode paths
- Web interface: FastAPI + Jinja2 + HTMX; no CLI entry points
- Hardware-adaptive: `config/requirements.py` detects CPU/GPU/VRAM and auto-selects a 5-tier profile

## Deployment

- **Default**: `docker compose up -d` (Docker required)
- **Fallback**: `python app.py` (Python 3.13+ + FFmpeg required)
- GPU tasks run on the host machine via `worker.py` (:9001), not inside the Docker container

## Testing

- **Use `tmp_path` fixture** for all temporary files and directories — never hardcode absolute paths like `/tmp/` or `/fake/`. On Windows, absolute paths resolve relative to the current drive and leak real directories on disk.
- **Mock all Path side effects** when patching `Path` methods: if you mock `Path.exists`, also mock `Path.mkdir`, `Path.write_text`, `Path.unlink`, etc. — any unmocked method runs for real and can create/delete files on the actual filesystem.
- Tests must be runnable without network access (mock external APIs, HuggingFace Hub, Ollama, etc.).
