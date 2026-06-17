# external/ — Vendored third-party projects

Two standalone open-source projects were installed (vendored) into this repo at
the user's request. Each lives in its own subdirectory with its upstream source
intact (nested `.git` removed so they are tracked as part of OpenClaw). They are
**independent applications** — they do not import from or integrate with the
ClawBot trading code. Run them on their own.

| Project | Path | Stack | Purpose |
|---|---|---|---|
| MoneyPrinterTurbo | `external/MoneyPrinterTurbo` | Python 3.11 (FastAPI + Streamlit) | AI-generated short videos from a topic/keyword |
| Flowsint | `external/flowsint` | TypeScript + Python monorepo (Next.js, Neo4j, Postgres, Redis) | Graph-based OSINT / investigation platform |

Upstream sources:
- MoneyPrinterTurbo — https://github.com/harry0703/MoneyPrinterTurbo
- Flowsint — https://github.com/reconurge/flowsint

---

## MoneyPrinterTurbo

Generates narrated short-form videos. Needs `ffmpeg` and ImageMagick on the host
(or just use the bundled Docker image). Bundled `resource/fonts/*.ttc` and
`resource/songs/*.mp3` are subtitle fonts and background music shipped by upstream.

### Quick start (Docker — recommended)
```bash
cd external/MoneyPrinterTurbo
cp config.example.toml config.toml      # then edit: add Pexels/Pixabay + LLM API keys
docker compose up
# WebUI:  http://localhost:8501
# API:    http://localhost:8080/docs
```

### Quick start (local Python)
```bash
cd external/MoneyPrinterTurbo
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # or: uv sync
cp config.example.toml config.toml       # add your API keys
streamlit run ./webui/Main.py            # WebUI
# or:  python main.py                     # API server
```

Set at least one stock-video source key (`pexels_api_keys` / `pixabay_api_keys`)
and an LLM provider in `config.toml` before generating.

---

## Flowsint

OSINT graph-investigation platform. **Read `external/flowsint/ETHICS.md` and
`DISCLAIMER.md` before use — it is for authorized, ethical investigation only.**
Runs as a multi-service Docker stack (web app, Python API, Neo4j, Postgres, Redis).

### Quick start (Docker — recommended)
```bash
cd external/flowsint
cp .env.example .env
# IMPORTANT: change AUTH_SECRET, MASTER_VAULT_KEY_V1, and NEO4J_PASSWORD before
# any real use — the values in .env.example are public placeholders.
# Generate a vault key:
#   python3 -c "import os,base64; print('base64:'+base64.b64encode(os.urandom(32)).decode())"
make prod        # production stack (docker-compose.prod.yml)
# or for development:
make dev         # docker-compose.dev.yml
```

App is served on the port defined by the compose file (see
`docker-compose.prod.yml`). Neo4j migrations live in `neo4j-migrations/`.

---

## Notes / maintenance
- These trees are pinned to the upstream `HEAD` at the time of install. To update,
  re-pull from upstream and replace the directory contents.
- Large binary assets (≈144 MB of fonts + sample audio in MoneyPrinterTurbo) come
  from upstream and are required for that tool to render subtitles.
- Nothing here is wired into `start.py` or the ClawBot scheduler.
