"""OpenClaw Local Dashboard — http://localhost:8080

Runs alongside the Telegram bot as a separate process.
Reads the bot's data files but never writes them — safe to run concurrently.
(The Hermes Mission Control panel writes only to its own data/hermes/ namespace,
so it never collides with the bot's files.)

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
from flask import Flask, jsonify, redirect, render_template_string, request

# Make project root importable so `core.hermes` resolves when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))
from core import hermes

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

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
  /* ── Hermes Mission Control ── */
  .hermes-head { display: flex; align-items: baseline; gap: 12px; margin: 28px 0 12px; }
  .hermes-head h2 { color: #7aa2ff; font-size: 1.1rem; letter-spacing: 0.5px; }
  .hermes-head .sub { color: #555; font-size: 0.75rem; }
  .stat-strip { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
  .stat { background: #15151f; border: 1px solid #262636; border-radius: 8px; padding: 10px 16px; min-width: 90px; }
  .stat .n { font-size: 1.4rem; font-family: monospace; font-weight: bold; }
  .stat .l { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 1px; color: #666; }
  .brief { background: linear-gradient(135deg,#141426,#1a1a2e); border: 1px solid #2d2d4a; border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
  .brief .who { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1px; color: #7aa2ff; margin-bottom: 8px; }
  .brief .body { font-size: 0.92rem; line-height: 1.5; color: #d8d8e8; }
  .agent { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #222; }
  .agent:last-child { border-bottom: none; }
  .agent .meta { font-size: 0.72rem; color: #666; }
  .pill { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.5px; }
  .pill.online  { background: #00ff8820; color: #00ff88; }
  .pill.busy    { background: #00ff8820; color: #00ff88; }
  .pill.idle    { background: #ffaa0020; color: #ffaa00; }
  .pill.error   { background: #ff444420; color: #ff4444; }
  .pill.offline { background: #44444430; color: #888; }
  .mission { display: flex; align-items: center; gap: 8px; padding: 7px 0; border-bottom: 1px solid #1e1e1e; font-size: 0.85rem; }
  .mission:last-child { border-bottom: none; }
  .mform { display: flex; gap: 6px; margin-top: 12px; }
  .mform input { flex: 1; background: #0d0d0d; border: 1px solid #2a2a2a; border-radius: 6px; padding: 6px 10px; color: #e0e0e0; font-size: 0.8rem; }
  .mform button, .mbtn { background: #1f3a5f; color: #7aa2ff; border: 1px solid #2d4a7a; border-radius: 6px; padding: 6px 12px; font-size: 0.78rem; cursor: pointer; }
  .mform button:hover, .mbtn:hover { background: #264a78; }
</style>
</head>
<body>

<h1>🦾 OpenClaw Dashboard</h1>
<p class="subtitle">
  Last updated: {{ now }}
  &nbsp;·&nbsp; Auto-refresh in <span id="cd">30</span>s
</p>

<!-- ════════ HERMES MISSION CONTROL ════════ -->
<div class="hermes-head">
  <h2>🛰️ Hermes Mission Control</h2>
  <span class="sub">single pane of glass across the OpenClaw agent fleet</span>
</div>

<div class="stat-strip">
  <div class="stat"><div class="n green">{{ fleet.online }}</div><div class="l">Online</div></div>
  <div class="stat"><div class="n amber">{{ fleet.idle }}</div><div class="l">Idle</div></div>
  <div class="stat"><div class="n">{{ fleet.offline }}</div><div class="l">Offline</div></div>
  <div class="stat"><div class="n {% if fleet.errors %}red{% endif %}">{{ fleet.errors }}</div><div class="l">Errors</div></div>
  <div class="stat"><div class="n">{{ fleet.tasks_completed }}</div><div class="l">Tasks</div></div>
  <div class="stat"><div class="n {% if fleet.cost_usd > 0.01 %}amber{% else %}green{% endif %}">${{ "%.4f"|format(fleet.cost_usd) }}</div><div class="l">Cost</div></div>
</div>

<div class="brief">
  <div class="who">🧠 Hermes AI Briefing
    <span class="mbtn" style="float:right;padding:1px 8px" onclick="loadBrief(true)">↻ refresh</span>
  </div>
  <div class="body" id="brief">Loading fleet briefing…</div>
</div>

<div class="grid">

  <!-- AGENT FLEET -->
  <div class="card">
    <h2>🤖 Agent Fleet</h2>
    {% if agents %}
      {% for a in agents %}
      <div class="agent">
        <div>
          <span class="pill {{ a.live_status }}">{{ a.live_status }}</span>
          &nbsp;<strong>{{ a.name }}</strong>
          <div class="meta">{{ a.current_task or '—' }} · {{ a.last_seen_label }} · {{ a.source }}</div>
        </div>
        <div style="text-align:right">
          <div class="val">{{ a.tasks_completed or 0 }} tasks</div>
          <div class="meta">${{ "%.4f"|format(a.cost_usd or 0) }}</div>
        </div>
      </div>
      {% endfor %}
    {% else %}
      <p class="empty">No agents reporting. Bots POST heartbeats to <code>/api/agents/state</code>.</p>
    {% endif %}
  </div>

  <!-- MISSION BACKLOG -->
  <div class="card">
    <h2>🎯 Mission Backlog</h2>
    {% if missions %}
      {% for m in missions %}
      <div class="mission">
        <span class="pill {{ 'online' if m.status=='active' else ('offline' if m.status=='done' else 'idle') }}">{{ m.status }}</span>
        <span style="flex:1">{{ m.title }}{% if m.agent %} <span class="meta">· {{ m.agent }}</span>{% endif %}</span>
        {% if m.status != 'done' %}
        <form method="post" action="/hermes/mission/{{ m.id }}/{{ 'active' if m.status=='backlog' else 'done' }}" style="margin:0">
          <button class="mbtn" type="submit">{{ '▶ start' if m.status=='backlog' else '✓ done' }}</button>
        </form>
        {% endif %}
      </div>
      {% endfor %}
    {% else %}
      <p class="empty">No missions yet. Add one below.</p>
    {% endif %}
    <form class="mform" method="post" action="/hermes/mission">
      <input name="title" placeholder="New mission…" required>
      <input name="agent" placeholder="agent" style="max-width:90px">
      <button type="submit">+ Add</button>
    </form>
  </div>

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

  // Lazy-load the Hermes AI briefing so the LLM call never blocks page render.
  function loadBrief(force) {
    const b = document.getElementById('brief');
    if (force) b.textContent = 'Thinking…';
    fetch('/hermes/ai' + (force ? '?force=1' : ''))
      .then(r => r.json())
      .then(d => { b.textContent = d.text + (d.age ? '  ·  ' + d.age : ''); })
      .catch(() => { b.textContent = 'Briefing unavailable.'; });
  }
  loadBrief(false);
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    usage  = get_usage_today()
    prices = get_prices()
    ollama = get_ollama_status()
    bot    = get_clawbot_status()
    tasks  = get_tasks()
    trades = get_recent_trades()
    cache  = get_cache_info()
    claude_ok = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return render_template_string(
        DASHBOARD_HTML,
        usage=usage, prices=prices, ollama=ollama, bot=bot,
        tasks=tasks, trades=trades, cache=cache,
        claude_ok=claude_ok, now=now,
        agents=hermes.get_agents(),
        fleet=hermes.fleet_summary(),
        missions=hermes.get_missions(),
    )


# ── Hermes Mission Control endpoints ──────────────────────────────────────

def _hermes_authorized() -> bool:
    """Heartbeat auth: open if HERMES_TOKEN unset, else require Bearer match."""
    token = os.getenv("HERMES_TOKEN", "").strip()
    if not token:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {token}" or request.headers.get("X-Hermes-Token", "") == token


@app.route("/api/agents/state", methods=["POST"])
def api_agent_state():
    """Hermes-compatible heartbeat ingestion. Agents POST their status here."""
    if not _hermes_authorized():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    agent_id = data.get("id") or data.get("agent_id") or data.get("name")
    if not agent_id:
        return jsonify({"error": "id (or name) is required"}), 400
    try:
        rec = hermes.record_heartbeat(
            str(agent_id),
            name=data.get("name"),
            status=data.get("status", "online"),
            current_task=data.get("current_task", "") or data.get("task", ""),
            tasks_completed=data.get("tasks_completed"),
            cost_usd=data.get("cost_usd") or data.get("cost"),
            meta=data.get("meta"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "agent": rec})


@app.route("/api/hermes/state")
def api_hermes_state():
    """Read-only fleet snapshot as JSON."""
    return jsonify({
        "fleet": hermes.fleet_summary(),
        "agents": hermes.get_agents(),
        "missions": hermes.get_missions(),
    })


@app.route("/hermes/ai")
def hermes_ai():
    """Lazy AI briefing (LLM call) — fetched async so it never blocks render."""
    return jsonify(hermes.hermes_ai_briefing(force=request.args.get("force") == "1"))


@app.route("/hermes/mission", methods=["POST"])
def hermes_add_mission():
    title = (request.form.get("title") or "").strip()
    if title:
        hermes.add_mission(title, agent=request.form.get("agent", ""),
                           notes=request.form.get("notes", ""))
    return redirect("/")


@app.route("/hermes/mission/<mission_id>/<status>", methods=["POST"])
def hermes_set_mission(mission_id: str, status: str):
    try:
        hermes.set_mission_status(mission_id, status)
    except ValueError:
        pass
    return redirect("/")


if __name__ == "__main__":
    import sys
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    port = int(os.getenv("PORT", 8080))
    print(f"OpenClaw Dashboard running at http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
