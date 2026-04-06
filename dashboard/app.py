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

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env", override=True)

app = Flask(__name__)
_price_cache: dict = {"ts": 0, "data": {}}


# ── Data helpers ───────────────────────────────────────────────────────────────

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
    """Parse trades.log — supports both JSONL and legacy prefixed format."""
    log = DATA_DIR / "logs" / "trades.log"
    if not log.exists():
        return []
    try:
        results = []
        for line in log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            # JSONL format (new)
            if line.startswith("{"):
                raw = line
            # Legacy "TRADE_DECISION | ts | {...}" format
            elif "|" in line:
                raw = line.split("|", 2)[-1].strip()
            else:
                continue
            try:
                t = json.loads(raw)
                results.append(t)
            except Exception:
                continue
        return results[-n:]
    except Exception:
        return []


def get_autotrade_status() -> dict:
    cfg = _read_json(DATA_DIR / "autotrade.json", {})
    return cfg


def get_backtest_summary() -> dict:
    results = _read_json(DATA_DIR / "backtest_results.json", {})
    if not results or not results.get("overall_ranking"):
        return {}
    top = results["overall_ranking"][0]
    return {
        "top_strategy": top.get("strategy", ""),
        "top_pair": top.get("pair", ""),
        "top_return": top.get("total_return_pct", 0),
        "top_winrate": top.get("win_rate", 0),
        "generated": results.get("generated_at", "")[:10],
        "period_days": results.get("period_days", 0),
        "ranking": results["overall_ranking"][:5],
    }


def get_notes_summary() -> dict:
    notes_file = DATA_DIR / "knowledge" / "notes.json"
    notes = _read_json(notes_file, [])
    return {
        "count": len(notes),
        "recent": notes[:4],
    }


def get_last_code_review() -> dict:
    reviews_dir = DATA_DIR / "code_reviews"
    if not reviews_dir.exists():
        return {}
    reports = sorted(reviews_dir.glob("*.md"), reverse=True)
    if not reports:
        return {}
    latest = reports[0]
    lines  = latest.read_text(encoding="utf-8").splitlines()
    return {
        "date": latest.stem,
        "preview": " ".join(l.strip() for l in lines[3:8] if l.strip())[:200],
    }


def get_prices() -> dict:
    import time
    import requests as req
    global _price_cache
    if time.time() - _price_cache["ts"] < 20:
        return _price_cache["data"]
    try:
        r = req.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,solana,ripple&vs_currencies=usd&include_24hr_change=true",
            timeout=8,
        )
        if r.status_code == 200:
            raw = r.json()
            data = {}
            for coin, cid in [("BTC","bitcoin"),("ETH","ethereum"),("SOL","solana"),("XRP","ripple")]:
                d   = raw.get(cid, {})
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
            "online": True, "models": models,
            "active": cfg if cfg in models else (models[0] if models else "none"),
            "cfg_missing": cfg not in models,
        }
    except Exception as exc:
        return {"online": False, "models": [], "active": "offline", "cfg_missing": False, "error": str(exc)[:60]}


def get_clawbot_status() -> dict:
    sentinel = DATA_DIR / "usage_stats.json"
    if not sentinel.exists():
        sentinel = DATA_DIR / "conversation_history.json"
    if not sentinel.exists():
        return {"running": False, "last_seen": "never"}
    mtime = sentinel.stat().st_mtime
    age   = datetime.now(timezone.utc).timestamp() - mtime
    if age < 300:
        return {"running": True, "last_seen": f"{int(age)}s ago"}
    mins  = int(age // 60)
    hrs   = mins // 60
    label = f"{hrs}h {mins % 60}m ago" if hrs else f"{mins}m ago"
    return {"running": age < 3600, "last_seen": label}


def get_cache_info() -> dict:
    cache = _read_json(DATA_DIR / "response_cache.json", {})
    if not cache:
        return {"entries": 0, "newest": "—"}
    newest_ts = max((v.get("ts", 0) for v in cache.values()), default=0)
    age = int((datetime.now(timezone.utc).timestamp() - newest_ts) // 60) if newest_ts else 0
    return {"entries": len(cache), "newest": f"{age}m ago" if newest_ts else "—"}


# ── HTML template ──────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OpenClaw Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d0d0d; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; padding: 24px; }
  h1  { font-size: 1.5rem; color: #00ff88; margin-bottom: 2px; }
  .subtitle { font-size: 0.78rem; color: #444; margin-bottom: 22px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
  .card { background: #141414; border: 1px solid #242424; border-radius: 10px; padding: 18px; }
  .card h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1.2px; color: #444; margin-bottom: 14px; }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid #1e1e1e; font-size: 0.85rem; }
  .row:last-child { border-bottom: none; }
  .label { color: #666; }
  .val   { font-family: monospace; }
  .green { color: #00ff88; }
  .red   { color: #ff4455; }
  .amber { color: #ffaa00; }
  .blue  { color: #4499ff; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; }
  .dot.green { background: #00ff88; } .dot.red { background: #ff4455; } .dot.amber { background: #ffaa00; }
  .price-row { padding: 8px 0; border-bottom: 1px solid #1e1e1e; }
  .price-row:last-child { border-bottom: none; }
  .coin  { font-size: 0.7rem; color: #555; letter-spacing: 1px; }
  .price { font-size: 1.25rem; font-family: monospace; font-weight: bold; }
  .chg   { font-size: 0.78rem; font-family: monospace; margin-left: 6px; }
  table  { width: 100%; border-collapse: collapse; font-size: 0.79rem; }
  th { text-align: left; color: #444; font-weight: 500; padding: 4px 6px; border-bottom: 1px solid #222; }
  td { padding: 6px 6px; border-bottom: 1px solid #1a1a1a; vertical-align: top; word-break: break-word; }
  tr:last-child td { border-bottom: none; }
  .empty { color: #333; font-size: 0.8rem; margin-top: 6px; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 0.68rem; font-family: monospace; }
  .badge.green { background: #00ff8818; color: #00ff88; border: 1px solid #00ff8830; }
  .badge.red   { background: #ff445518; color: #ff4455; border: 1px solid #ff445530; }
  .badge.amber { background: #ffaa0018; color: #ffaa00; border: 1px solid #ffaa0030; }
  .badge.blue  { background: #4499ff18; color: #4499ff; border: 1px solid #4499ff30; }
  .note-item { padding: 7px 0; border-bottom: 1px solid #1a1a1a; font-size: 0.8rem; }
  .note-item:last-child { border-bottom: none; }
  .note-title { color: #ccc; }
  .note-meta  { color: #444; font-size: 0.7rem; margin-top: 2px; }
  .full-width { grid-column: 1 / -1; }
</style>
</head>
<body>

<h1>🦾 OpenClaw Dashboard</h1>
<p class="subtitle">{{ now }} &nbsp;·&nbsp; Auto-refresh <span id="cd">30</span>s</p>

<div class="grid">

<!-- SYSTEM STATUS -->
<div class="card">
  <h2>System Status</h2>
  <div class="row">
    <span class="label">ClawBot</span>
    <span class="val">
      {% if bot.running %}<span class="dot green"></span><span class="green">Active</span>
      {% else %}<span class="dot amber"></span><span class="amber">Idle</span>{% endif %}
      <span style="color:#333;font-size:0.72rem"> {{ bot.last_seen }}</span>
    </span>
  </div>
  <div class="row">
    <span class="label">Ollama</span>
    <span class="val">
      {% if ollama.online %}<span class="green">online ✅</span>
      {% else %}<span class="red">offline ❌</span>{% endif %}
    </span>
  </div>
  {% if ollama.models %}
  <div class="row">
    <span class="label">Model</span>
    <span class="val" style="color:#555;font-size:0.78rem">{{ ollama.active }}</span>
  </div>
  {% endif %}
  <div class="row">
    <span class="label">Claude API</span>
    <span class="val">{% if claude_ok %}<span class="green">configured ✅</span>
    {% else %}<span class="amber">not set ⚠️</span>{% endif %}</span>
  </div>
  <div class="row">
    <span class="label">Crypto.com</span>
    <span class="val">{% if crypto_ok %}<span class="green">configured ✅</span>
    {% else %}<span class="amber">not set ⚠️</span>{% endif %}</span>
  </div>
  <div class="row">
    <span class="label">Cache</span>
    <span class="val">{{ cache.entries }} entries &nbsp;<span style="color:#333">{{ cache.newest }}</span></span>
  </div>
</div>

<!-- AUTO-TRADE STATUS -->
<div class="card">
  <h2>Auto-Trade</h2>
  {% if autotrade.enabled %}
  <div class="row">
    <span class="label">Status</span>
    <span class="val"><span class="badge green">ENABLED</span></span>
  </div>
  <div class="row">
    <span class="label">Daily scan</span>
    <span class="val green">{{ autotrade.scan_time }} UTC</span>
  </div>
  <div class="row">
    <span class="label">Timeframe</span>
    <span class="val">{{ autotrade.timeframe }}</span>
  </div>
  <div class="row">
    <span class="label">Strategy</span>
    <span class="val" style="color:#555">RSI+MACD · 1.5% risk</span>
  </div>
  {% else %}
  <div style="padding:12px 0; color:#333; font-size:0.82rem">
    Auto-trade is <span class="amber">disabled</span>.<br>
    <span style="color:#2a2a2a; font-size:0.75rem">Send /autotrade on in Telegram to enable.</span>
  </div>
  {% endif %}

  <div class="row" style="margin-top:8px">
    <span class="label">Total logged trades</span>
    <span class="val {% if trades|length > 0 %}green{% else %}amber{% endif %}">{{ trades|length }}</span>
  </div>
</div>

<!-- LIVE PRICES -->
<div class="card">
  <h2>Live Prices</h2>
  {% if prices %}
    {% for coin, d in prices.items() %}
    <div class="price-row">
      <div class="coin">{{ coin }}/USDT</div>
      <span class="price">${{ "{:,.2f}".format(d.price) }}</span>
      <span class="chg {{ d.cls }}">{{ d.sign }}{{ d.change }}%</span>
    </div>
    {% endfor %}
  {% else %}
    <p class="empty">CoinGecko unavailable — refresh to retry</p>
  {% endif %}
</div>

<!-- BRAIN STATS -->
<div class="card">
  <h2>Brain Stats — Today</h2>
  <div class="row">
    <span class="label">Ollama (free)</span>
    <span class="val green">{{ usage.ollama_calls }} calls</span>
  </div>
  <div class="row">
    <span class="label">Claude Haiku</span>
    <span class="val {% if usage.claude_calls > 0 %}amber{% endif %}">{{ usage.claude_calls }} calls</span>
  </div>
  <div class="row">
    <span class="label">Cache hits</span>
    <span class="val green">{{ usage.cache_hits }} 💾</span>
  </div>
  <div class="row">
    <span class="label">Tokens in / out</span>
    <span class="val" style="color:#555">{{ "{:,}".format(usage.claude_input_tokens) }} / {{ "{:,}".format(usage.claude_output_tokens) }}</span>
  </div>
  <div class="row">
    <span class="label">API cost today</span>
    {% set cost = (usage.claude_input_tokens * 0.00000025) + (usage.claude_output_tokens * 0.00000125) %}
    <span class="val {% if cost > 0.01 %}amber{% else %}green{% endif %}">${{ "%.4f"|format(cost) }}</span>
  </div>
</div>

<!-- BACKTEST RESULTS -->
<div class="card">
  <h2>Backtest Results</h2>
  {% if backtest and backtest.ranking %}
    <div style="font-size:0.72rem;color:#444;margin-bottom:10px">
      {{ backtest.period_days // 365 }}Y history · generated {{ backtest.generated }}
    </div>
    <table>
      <tr><th>#</th><th>Strategy / Pair</th><th>Return</th><th>Win%</th></tr>
      {% for r in backtest.ranking %}
      <tr>
        <td style="color:#444">{{ loop.index }}</td>
        <td><span style="color:#ccc">{{ r.strategy }}</span><br>
            <span style="color:#444;font-size:0.7rem">{{ r.pair }}</span></td>
        <td class="{% if r.total_return_pct > 0 %}green{% else %}red{% endif %}">
          {{ "%+.0f"|format(r.total_return_pct) }}%</td>
        <td style="color:#888">{{ r.win_rate }}%</td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p class="empty">No backtest data yet.<br>
    <span style="color:#2a2a2a">Run /backtest run in Telegram.</span></p>
  {% endif %}
</div>

<!-- KNOWLEDGE NOTES -->
<div class="card">
  <h2>Knowledge Notes</h2>
  {% if notes.count > 0 %}
    <div style="font-size:0.72rem;color:#444;margin-bottom:10px">{{ notes.count }} saved notes</div>
    {% for n in notes.recent %}
    <div class="note-item">
      <div class="note-title">{{ n.title[:60] }}{% if n.title|length > 60 %}…{% endif %}</div>
      <div class="note-meta">
        {{ n.timestamp[:10] }}
        {% for tag in n.tags[:3] %}<span class="badge blue" style="margin-left:4px">#{{ tag }}</span>{% endfor %}
      </div>
    </div>
    {% endfor %}
  {% else %}
    <p class="empty">No notes yet.<br>
    <span style="color:#2a2a2a">Use /save in Telegram after a good conversation.</span></p>
  {% endif %}
</div>

<!-- REMINDERS -->
<div class="card">
  <h2>Pending Reminders</h2>
  {% if tasks %}
    <table>
      <tr><th>Time (UTC)</th><th>Reminder</th></tr>
      {% for t in tasks %}
      <tr>
        <td style="color:#00ff88;white-space:nowrap">{{ t.time }}</td>
        <td>{{ t.text }}</td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p class="empty">No pending reminders.<br>
    <span style="color:#2a2a2a">Use /remind HH:MM text in Telegram.</span></p>
  {% endif %}
</div>

<!-- CODE REVIEW -->
<div class="card">
  <h2>Last Code Review</h2>
  {% if codereview.date %}
    <div class="row">
      <span class="label">Date</span>
      <span class="val green">{{ codereview.date }}</span>
    </div>
    <div style="margin-top:10px;font-size:0.78rem;color:#555;line-height:1.5">
      {{ codereview.preview[:250] }}{% if codereview.preview|length > 250 %}…{% endif %}
    </div>
  {% else %}
    <p class="empty">No code reviews yet.<br>
    <span style="color:#2a2a2a">Run /codereview run in Telegram.</span></p>
  {% endif %}
</div>

<!-- RECENT TRADES (full width) -->
<div class="card full-width">
  <h2>Recent Trade Log</h2>
  {% if trades %}
    <table>
      <tr><th>Time</th><th>Coin</th><th>Action</th><th>Conf</th><th>USD</th><th>Status</th><th>Notes</th></tr>
      {% for t in trades|reverse %}
      <tr>
        <td style="color:#444;white-space:nowrap;font-size:0.75rem">{{ t.get('timestamp','')[:16]|replace('T',' ') }}</td>
        <td style="color:#ccc">{{ t.get('coin', t.get('action','?')) }}</td>
        <td>
          {% set act = t.get('action','') %}
          <span class="badge {% if act == 'BUY' %}green{% elif act == 'SELL' %}red{% else %}amber{% endif %}">
            {{ act or '—' }}
          </span>
        </td>
        <td style="color:#555">{{ t.get('confidence','—') }}</td>
        <td style="font-family:monospace">${{ "%.2f"|format(t.get('usd_amount',0)|float) }}</td>
        <td>
          {% set st = t.get('status','') %}
          <span class="badge {% if st == 'executed' %}green{% elif st == 'error' %}red{% else %}amber{% endif %}">
            {{ st or '—' }}
          </span>
        </td>
        <td style="color:#444;font-size:0.75rem">{{ t.get('reason', t.get('notes',''))[:60] }}</td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p class="empty">No trades logged yet. Enable /autotrade on in Telegram to start collecting data.</p>
  {% endif %}
</div>

</div><!-- /grid -->

<script>
  let t = 30;
  const el = document.getElementById('cd');
  setInterval(() => { t--; if (t <= 0) location.reload(); else el.textContent = t; }, 1000);
</script>
</body>
</html>"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    usage      = get_usage_today()
    prices     = get_prices()
    ollama     = get_ollama_status()
    bot        = get_clawbot_status()
    tasks      = get_tasks()
    trades     = get_recent_trades()
    cache      = get_cache_info()
    autotrade  = get_autotrade_status()
    backtest   = get_backtest_summary()
    notes      = get_notes_summary()
    codereview = get_last_code_review()
    claude_ok  = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    crypto_ok  = bool(os.getenv("CRYPTOCOM_API_KEY", "").strip())
    now        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return render_template_string(
        DASHBOARD_HTML,
        usage=usage, prices=prices, ollama=ollama, bot=bot,
        tasks=tasks, trades=trades, cache=cache,
        autotrade=autotrade, backtest=backtest,
        notes=notes, codereview=codereview,
        claude_ok=claude_ok, crypto_ok=crypto_ok, now=now,
    )


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("OpenClaw Dashboard → http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
