# OpenClaw (ClawBot)

Personal trading bot and content pipeline powered by a hybrid AI brain (Ollama + Claude Haiku).

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) installed and running locally
- Ollama model pulled: `ollama pull qwen2.5:14b`
- `ffmpeg` on PATH (content pipeline only)

## Setup

1. **Clone the repo**
   ```bash
   git clone <repo-url>
   cd openclaw
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

4. **Start Ollama** (in a separate terminal)
   ```bash
   ollama serve
   ```

## Running

```bash
# Telegram bot (main entry point)
python -m content.receiver

# Local dashboard (run alongside the bot)
python dashboard/app.py

# Content pipeline — GDrive watcher mode
python -m content.pipeline

# Content pipeline — single file
python -m content.pipeline --once path/to/video.mp4
```

## Project Structure

```
├── core/
│   ├── brain.py          - Hybrid AI router (Ollama → Claude Haiku)
│   ├── conversation.py   - Per-chat conversation history (JSON, 10 turns)
│   ├── market.py         - CoinGecko prices + LLM analysis
│   ├── scheduler.py      - APScheduler: reminders + daily auto-trade job
│   └── startup.py        - Data directory initialisation
├── trading/
│   ├── strategy.py       - RSI + MACD signal engine
│   ├── executor.py       - Trade execution (Crypto.com)
│   └── exchange.py       - Crypto.com public + private API connector
├── content/
│   ├── receiver.py       - Telegram bot (main entry point)
│   ├── pipeline.py       - Content pipeline orchestrator
│   ├── watcher.py        - Google Drive folder watcher (watchdog)
│   ├── editor.py         - FFmpeg + Whisper video editor
│   ├── caption_generator.py  - Ollama-powered caption writer
│   ├── uploader.py       - Telegram approval sender
│   ├── poster.py         - TikTok + Instagram publisher
│   └── music/            - Background music tracks
├── security/
│   └── whitelist.py      - Telegram chat ID allowlist
├── dashboard/
│   └── app.py            - Flask dashboard (localhost:8080)
├── data/                 - Runtime data (gitignored)
│   ├── logs/trades.log
│   ├── response_cache.json
│   ├── usage_stats.json
│   ├── tasks.json
│   ├── autotrade.json
│   └── conversation_history.json
└── .env.example
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `[any message]` | Chat with AI brain |
| `/plan [idea]` | Structured business plan |
| `/research [topic]` | Deep research breakdown |
| `/market` | BTC/ETH/SOL live prices + analysis |
| `/scan [1h\|4h\|1d]` | RSI+MACD live signal scan |
| `/remind HH:MM text` | Set a daily reminder (UTC) |
| `/tasks` | List pending reminders |
| `/cancel <id>` | Cancel a reminder |
| `/autotrade on` | Enable daily auto-trade at 08:00 UTC |
| `/autotrade off` | Disable auto-trade |
| `/status` | Bot + Ollama health check |
| `/brain` | AI usage stats |
| `/clear` | Reset conversation memory |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OLLAMA_MODEL` | Ollama model to use | `qwen2.5:14b` |
| `ANTHROPIC_API_KEY` | Claude API key (optional) | — |
| `USE_CLAUDE_API` | Enable Claude for complex tasks | `true` |
| `MAX_TOKENS_PER_RESPONSE` | Max tokens per Claude response | `500` |
| `COMPLEXITY_THRESHOLD` | Word count for Claude routing | `50` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | — |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for notifications | — |
| `ALLOWED_CHAT_ID` | Comma-separated authorized chat IDs | — |
| `CRYPTOCOM_API_KEY` | Crypto.com API key | — |
| `CRYPTOCOM_SECRET` | Crypto.com API secret | — |
| `GDRIVE_WATCH_FOLDER` | Local GDrive folder to watch | — |
| `MUSIC_FOLDER` | Background music for reels | `content/music` |
| `WHISPER_MODEL` | Whisper model size | `base` |
| `TIKTOK_ACCESS_TOKEN` | TikTok Content Posting API token | — |
| `INSTAGRAM_ACCESS_TOKEN` | Meta Graph API token | — |
| `INSTAGRAM_USER_ID` | Instagram Business/Creator account ID | — |

## Notes

- Never commit your `.env` file — it is gitignored.
- `ALLOWED_CHAT_ID` must be set or the bot silently ignores all messages.
- Logs are written to `data/logs/`.
- The dashboard reads data files but never writes — safe to run concurrently with the bot.
