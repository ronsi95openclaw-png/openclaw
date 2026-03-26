# OpenClaw (ClawBot) v0.1

Personal trading bot system powered by a local Ollama LLM.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) installed and running locally
- Ollama model pulled: `ollama pull qwen2.5:14b`

## Setup

1. **Clone the repo**
   ```bash
   git clone <repo-url>
   cd Claude-openclaw
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

5. **Test the LLM brain**
   ```bash
   python test_brain.py
   ```

## Usage

```bash
python main.py dca      # Run one DCA cycle (Crypto.com)
python main.py futures  # Run one futures cycle (Blofin)
```

## Project Structure

```
├── bots/
│   ├── dca/         - DCA bot for Crypto.com
│   └── futures/     - Futures trading bot for Blofin
├── core/
│   ├── brain.py     - LLM interface (Ollama)
│   └── logger.py    - Trade logging
├── config/
│   └── settings.py  - Env-based configuration
├── data/logs/       - Trade decision logs
├── .env.example     - Environment variable template
└── main.py          - CLI entry point
```

## Environment Variables

| Variable            | Description                        | Default        |
|---------------------|------------------------------------|----------------|
| `OLLAMA_MODEL`      | Ollama model to use                | `qwen2.5:14b`  |
| `CRYPTOCOM_API_KEY` | Crypto.com API key                 | —              |
| `CRYPTOCOM_SECRET`  | Crypto.com API secret              | —              |
| `BLOFIN_API_KEY`    | Blofin API key                     | —              |
| `BLOFIN_SECRET`     | Blofin API secret                  | —              |

## Notes

- All trade decisions are confirmed by the LLM before execution.
- Never commit your `.env` file — it's gitignored.
- Logs are written to `data/logs/`.
