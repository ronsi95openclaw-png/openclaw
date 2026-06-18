"""OpenClaw Local Dashboard — http://localhost:8080

Runs alongside the Telegram bot as a separate process.
Reads data files but never writes — safe to run concurrently.

Usage:
    python dashboard/app.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template_string

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

# Ensure repo-root packages (e.g. hermes/) are importable when this file is run
# directly as `python dashboard/app.py` (which puts dashboard/ on sys.path).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── In-process price cache (avoid hammering CoinGecko) ───────────────────────
_price_cache: dict = {"ts": 0, "data": {}}


# ── Data helpers ──────────────────────────────────────────────────────────────

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def get_usage_today() -> dict:
    stats = _read_json(DATA_DIR / "usage_stats.json", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return stats.get(today, {
        "ollama_calls": 0, "claude_calls": 0,
        "claude_input_tokens": 0, "claude_output_tokens": 0, "cache_hits": 0,
    })


def get_tasks() -> list:
    tasks = _read_json(DATA_DIR / "tasks.json", [])
    return [t for t in tasks if t.get("status") == "pending"]


def get_recent_trades(n: int = 15) -> list:
    log = DATA_DIR / "logs" / "trades.log"
    if not log.exists():
        return []
    try:
        lines = [l for l in log.read_text(encoding="utf-8").splitlines()
                 if "TRADE_DECISION" in l][-n:]
        result = []
        for raw in lines:
            parts = raw.split(" | ")
            ts  = parts[1].replace("T", " ")[:16] if len(parts) > 1 else ""
            dec = parts[2][:120] if len(parts) > 2 else raw[:120]
            result.append({"ts": ts, "decision": dec})
        return result
    except Exception:
        return []


def get_prices() -> dict:
    import time, requests as req
    global _price_cache
    if time.time() - _price_cache["ts"] < 20:
        return _price_cache["data"]
    try:
        r = req.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true",
            timeout=8,
        )
        if r.status_code == 200:
            raw = r.json()
            data = {}
            for coin, cid in [("BTC","bitcoin"),("ETH","ethereum"),("SOL","solana")]:
                d = raw.get(cid, {})
                chg = d.get("usd_24h_change", 0) or 0
                data[coin] = {
                    "price":  d.get("usd", 0),
                    "change": round(chg, 2),
                    "cls":    "green" if chg >= 0 else "red",
                    "sign":   "+" if chg >= 0 else "",
                }
            _price_cache = {"ts": time.time(), "data": data}
            return data
    except Exception:
        pass
    return {}


def get_ollama_status() -> dict:
    try:
        from ollama import list as _ol_list
        models = [m.model for m in _ol_list().models]
        cfg    = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
        return {
            "online": True,
            "models": models,
            "active": cfg if cfg in models else (models[0] if models else "none"),
            "cfg_missing": cfg not in models,
        }
    except Exception as exc:
        return {"online": False, "models": [], "active": "offline", "cfg_missing": False, "error": str(exc)[:60]}


def get_clawbot_status() -> dict:
    hist = DATA_DIR / "conversation_history.json"
    stats = DATA_DIR / "usage_stats.json"
    sentinel = hist if hist.exists() else (stats if stats.exists() else None)
    if sentinel is None:
        return {"running": False, "last_seen": "never"}
    mtime = sentinel.stat().st_mtime
    age   = datetime.now(timezone.utc).timestamp() - mtime
    if age < 300:
        return {"running": True, "last_seen": f"{int(age)}s ago"}
    mins = int(age // 60)
    hrs  = mins // 60
    label = f"{hrs}h {mins % 60}m ago" if hrs else f"{mins}m ago"
    return {"running": False, "last_seen": label}


def get_cache_info() -> dict:
    cache = _read_json(DATA_DIR / "response_cache.json", {})
    if not cache:
        return {"entries": 0, "newest": "—"}
    newest_ts = max((v.get("ts", 0) for v in cache.values()), default=0)
    if newest_ts:
        age = int((datetime.now(timezone.utc).timestamp() - newest_ts) // 60)
        newest = f"{age}m ago"
    else:
        newest = "—"
    return {"entries": len(cache), "newest": newest}


# ── Multi-bot health (driven by hermes/health.py) ─────────────────────────────
# Every helper is wrapped so a missing file or import never 500s the dashboard.

def get_haulyeah_status() -> dict:
    """HaulYeah health via hermes.health — degrades gracefully if data absent."""
    try:
        from hermes.health import get_haulyeah_health
        return get_haulyeah_health()
    except Exception as exc:
        return {"name": "HaulYeah", "running": False, "status": "unknown",
                "last_seen": "never", "pending_outreach": 0, "leads": 0,
                "error": str(exc)[:60]}


def get_hermes_status() -> dict:
    """Hermes overseer health: report whether the overseer bot is configured and
    summarize the latest briefing it would emit. Never raises."""
    info = {
        "configured": bool(os.getenv("HERMES_BOT_TOKEN", "").strip()
                           and os.getenv("HERMES_CHAT_ID", "").strip()),
        "interval": os.getenv("HERMES_CHECK_INTERVAL_MINUTES", "30"),
        "alerts": [],
    }
    try:
        from hermes.health import get_all_health
        from hermes.briefing import _alerts
        info["alerts"] = _alerts(get_all_health())
    except Exception as exc:
        info["error"] = str(exc)[:60]
    return info


# ── HTML template ─────────────────────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OpenClaw Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d0d0d; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; padding: 20px; }
  h1 { font-size: 1.6rem; color: #00ff88; margin-bottom: 4px; }
  .subtitle { font-size: 0.8rem; color: #555; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 20px; }
  .card h2 { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; color: #555; margin-bottom: 14px; }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #222; font-size: 0.88rem; }
  .row:last-child { border-bottom: none; }
  .label { color: #888; }
  .val { font-family: monospace; }
  .green { color: #00ff88; }
  .red   { color: #ff4444; }
  .amber { color: #ffaa00; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.green { background: #00ff88; }
  .dot.red   { background: #ff4444; }
  .dot.amber { background: #ffaa00; }
  .price-block { padding: 10px 0; border-bottom: 1px solid #222; }
  .price-block:last-child { border-bottom: none; }
  .coin { font-size: 0.75rem; color: #555; }
  .price { font-size: 1.3rem; font-family: monospace; font-weight: bold; }
  .change { font-size: 0.8rem; font-family: monospace; margin-left: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; margin-top: 4px; }
  th { text-align: left; color: #555; font-weight: normal; padding: 4px 6px; border-bottom: 1px solid #2a2a2a; }
  td { padding: 6px 6px; border-bottom: 1px solid #1e1e1e; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .empty { color: #444; font-size: 0.8rem; margin-top: 8px; }
  .refresh { float: right; font-size: 0.75rem; color: #333; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; }
  .tag.green { background: #00ff8820; color: #00ff88; border: 1px solid #00ff8840; }
  .tag.red   { background: #ff444420; color: #ff4444; border: 1px solid #ff444440; }
  .tag.amber { background: #ffaa0020; color: #ffaa00; border: 1px solid #ffaa0040; }
</style>
</head>
<body>

<h1>🦾 OpenClaw Dashboard</h1>
<p class="subtitle">
  Multi-bot overview · ClawBot · HaulYeah · Hermes
  &nbsp;·&nbsp; Last updated: {{ now }}
  &nbsp;·&nbsp; Auto-refresh in <span id="cd">30</span>s
</p>

<div class="grid">

  <!-- BOT STATUS -->
  <div class="card">
    <h2>⚙️ System Status</h2>

    <div class="row">
      <span class="label">ClawBot</span>
      <span class="val">
        {% if bot.running %}
          <span class="dot green"></span><span class="green">Active</span>
          <span style="color:#444;font-size:0.75rem"> ({{ bot.last_seen }})</span>
        {% else %}
          <span class="dot amber"></span><span class="amber">Idle</span>
          <span style="color:#444;font-size:0.75rem"> ({{ bot.last_seen }})</span>
        {% endif %}
      </span>
    </div>

    <div class="row">
      <span class="label">Ollama</span>
      <span class="val">
        {% if ollama.online %}
          <span class="green">online ✅</span>
          {% if ollama.cfg_missing %}
            <span class="amber" style="font-size:0.75rem"> ({{ ollama.active }})</span>
          {% endif %}
        {% else %}
          <span class="red">offline ❌</span>
        {% endif %}
      </span>
    </div>

    {% if ollama.models %}
    <div class="row">
      <span class="label">Models</span>
      <span class="val" style="font-size:0.8rem;color:#666">{{ ollama.models | join(', ') }}</span>
    </div>
    {% endif %}

    <div class="row">
      <span class="label">Claude API</span>
      <span class="val">
        {% if claude_ok %}
          <span class="green">configured ✅</span>
        {% else %}
          <span class="amber">not set ⚠️</span>
        {% endif %}
      </span>
    </div>

    <div class="row">
      <span class="label">Response cache</span>
      <span class="val">{{ cache.entries }} entries &nbsp;<span style="color:#444">newest {{ cache.newest }}</span></span>
    </div>
  </div>

  <!-- LIVE PRICES -->
  <div class="card">
    <h2>📈 Live Prices</h2>
    {% if prices %}
      {% for coin, d in prices.items() %}
      <div class="price-block">
        <div class="coin">{{ coin }}</div>
        <div>
          <span class="price">${{ "{:,.2f}".format(d.price) }}</span>
          <span class="change {{ d.cls }}">{{ d.sign }}{{ d.change }}% 24h</span>
        </div>
      </div>
      {% endfor %}
    {% else %}
      <p class="empty">CoinGecko unavailable</p>
    {% endif %}
  </div>

  <!-- BRAIN STATS -->
  <div class="card">
    <h2>🧠 Brain Stats — Today</h2>
    <div class="row">
      <span class="label">Ollama calls</span>
      <span class="val">{{ usage.ollama_calls }}</span>
    </div>
    <div class="row">
      <span class="label">Claude calls</span>
      <span class="val {% if usage.claude_calls > 0 %}amber{% endif %}">{{ usage.claude_calls }}</span>
    </div>
    <div class="row">
      <span class="label">Cache hits</span>
      <span class="val green">{{ usage.cache_hits }} 💾</span>
    </div>
    <div class="row">
      <span class="label">Input tokens</span>
      <span class="val">{{ "{:,}".format(usage.claude_input_tokens) }}</span>
    </div>
    <div class="row">
      <span class="label">Output tokens</span>
      <span class="val">{{ "{:,}".format(usage.claude_output_tokens) }}</span>
    </div>
    <div class="row">
      <span class="label">Est. API cost</span>
      {% set cost = (usage.claude_input_tokens * 0.000001) + (usage.claude_output_tokens * 0.000005) %}
      <span class="val {% if cost > 0.01 %}amber{% else %}green{% endif %}">${{ "%.4f"|format(cost) }}</span>
    </div>
  </div>

  <!-- REMINDERS -->
  <div class="card">
    <h2>⏰ Pending Reminders</h2>
    {% if tasks %}
      <table>
        <tr><th>Time (UTC)</th><th>Reminder</th></tr>
        {% for t in tasks %}
        <tr>
          <td style="white-space:nowrap;color:#00ff88">{{ t.time }}</td>
          <td>{{ t.text }}</td>
        </tr>
        {% endfor %}
      </table>
    {% else %}
      <p class="empty">No pending reminders. Use /remind in Telegram.</p>
    {% endif %}
  </div>

  <!-- HAULYEAH -->
  <div class="card">
    <h2>🚛 HaulYeah</h2>
    <div class="row">
      <span class="label">Status</span>
      <span class="val">
        {% if haulyeah.running %}
          <span class="dot green"></span><span class="green">Active</span>
          <span style="color:#444;font-size:0.75rem"> ({{ haulyeah.last_seen }})</span>
        {% elif haulyeah.status == 'idle' %}
          <span class="dot amber"></span><span class="amber">Idle</span>
          <span style="color:#444;font-size:0.75rem"> ({{ haulyeah.last_seen }})</span>
        {% else %}
          <span class="dot red"></span><span class="red">Unknown</span>
        {% endif %}
      </span>
    </div>
    <div class="row">
      <span class="label">Leads</span>
      <span class="val">{{ haulyeah.leads }}</span>
    </div>
    <div class="row">
      <span class="label">Pending outreach</span>
      <span class="val {% if haulyeah.pending_outreach > 0 %}amber{% endif %}">{{ haulyeah.pending_outreach }}</span>
    </div>
    {% if haulyeah.error %}
    <div class="row">
      <span class="label">Note</span>
      <span class="val" style="font-size:0.75rem;color:#666">{{ haulyeah.error }}</span>
    </div>
    {% endif %}
  </div>

  <!-- HERMES -->
  <div class="card">
    <h2>🪽 Hermes Overseer</h2>
    <div class="row">
      <span class="label">Bot</span>
      <span class="val">
        {% if hermes.configured %}
          <span class="green">configured ✅</span>
        {% else %}
          <span class="amber">not set ⚠️</span>
        {% endif %}
      </span>
    </div>
    <div class="row">
      <span class="label">Check interval</span>
      <span class="val">{{ hermes.interval }} min</span>
    </div>
    {% if hermes.alerts %}
      {% for a in hermes.alerts %}
      <div class="row">
        <span class="label">Alert</span>
        <span class="val" style="font-size:0.8rem">{{ a }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div class="row">
        <span class="label">Alerts</span>
        <span class="val green">none — all nominal</span>
      </div>
    {% endif %}
  </div>

  <!-- RECENT TRADES -->
  <div class="card" style="grid-column: 1 / -1;">
    <h2>📊 Recent Trade Decisions</h2>
    {% if trades %}
      <table>
        <tr><th style="width:140px">Timestamp</th><th>Decision</th></tr>
        {% for t in trades %}
        <tr>
          <td style="color:#555;white-space:nowrap">{{ t.ts }}</td>
          <td>{{ t.decision }}</td>
        </tr>
        {% endfor %}
      </table>
    {% else %}
      <p class="empty">No trades logged yet. Run the DCA or Futures bot to generate decisions.</p>
    {% endif %}
  </div>

</div>

<script>
  let t = 30;
  const el = document.getElementById('cd');
  setInterval(() => {
    t--;
    if (t <= 0) { location.reload(); }
    else { el.textContent = t; }
  }, 1000);
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    usage    = get_usage_today()
    prices   = get_prices()
    ollama   = get_ollama_status()
    bot      = get_clawbot_status()
    haulyeah = get_haulyeah_status()
    hermes   = get_hermes_status()
    tasks    = get_tasks()
    trades   = get_recent_trades()
    cache    = get_cache_info()
    claude_ok = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return render_template_string(
        DASHBOARD_HTML,
        usage=usage, prices=prices, ollama=ollama, bot=bot,
        haulyeah=haulyeah, hermes=hermes,
        tasks=tasks, trades=trades, cache=cache,
        claude_ok=claude_ok, now=now,
    )


if __name__ == "__main__":
    import sys
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    port = int(os.getenv("PORT", 8080))
    print(f"OpenClaw Dashboard running at http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
