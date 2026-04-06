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
from flask import Flask, render_template_string, request, jsonify

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


def get_orchestration_tasks() -> list:
    """Get orchestration tasks for dashboard display."""
    try:
        from skills.agent_team_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        tasks = list(orchestrator.tasks.values())
        # Convert to dict format for template
        return [{
            "id": t.id,
            "title": t.title,
            "state": t.state,
            "assigned_to": t.assigned_to or "unassigned",
            "created_at": t.created_at[:16] if t.created_at else "",
            "priority": t.priority,
            "comments_count": len(t.comments)
        } for t in tasks]
    except Exception as e:
        print(f"Error loading orchestration tasks: {e}")
        return []


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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  body { background: linear-gradient(135deg, #0d0d0d 0%, #1a1a1a 100%); color: #e0e0e0; font-family: 'Segoe UI', sans-serif; }
  .navbar { background: #141414 !important; border-bottom: 1px solid #242424; }
  .card { background: #141414; border: 1px solid #242424; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s; }
  .card:hover { transform: translateY(-2px); }
  .card-header { background: #1e1e1e; border-bottom: 1px solid #242424; border-radius: 15px 15px 0 0 !important; }
  .card-body { padding: 1.5rem; }
  .btn-custom { background: linear-gradient(45deg, #00ff88, #00cc66); border: none; color: #000; font-weight: bold; }
  .btn-custom:hover { background: linear-gradient(45deg, #00cc66, #00aa55); color: #000; }
  .text-success { color: #00ff88 !important; }
  .text-danger { color: #ff4455 !important; }
  .text-warning { color: #ffaa00 !important; }
  .text-info { color: #4499ff !important; }
  .table { color: #e0e0e0; }
  .table thead th { border-bottom: 2px solid #242424; color: #00ff88; }
  .table tbody td { border-bottom: 1px solid #1e1e1e; }
  .badge { font-size: 0.75rem; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
  .dot.green { background: #00ff88; } .dot.red { background: #ff4455; } .dot.amber { background: #ffaa00; }
  .price-card { background: linear-gradient(45deg, #1e1e1e, #242424); }
  .chat-panel { position: fixed; bottom: 0; right: 0; width: 400px; background: #0f0f0f; border: 1px solid #242424; border-bottom: none; border-right: none; border-radius: 15px 0 0 0; box-shadow: -4px -4px 24px #00000088; z-index: 100; }
  .chat-header { background: #1a1a1a; border-bottom: 1px solid #1e1e1e; border-radius: 15px 0 0 0; }
  .chat-messages { max-height: 350px; overflow-y: auto; }
  .msg-user { align-self: flex-end; background: linear-gradient(45deg, #00ff8812, #00cc6612); border: 1px solid #00ff8822; }
  .msg-bot { background: #1a1a1a; border: 1px solid #242424; }
  .auto-refresh { font-size: 0.8rem; color: #666; }
  .section-icon { margin-right: 8px; }
  .collapsible { cursor: pointer; }
  .collapsible:hover { color: #00ff88; }
</style>
</head>
<body class="bg-dark">

<nav class="navbar navbar-expand-lg navbar-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="#"><i class="fas fa-robot"></i> OpenClaw Dashboard</a>
    <span class="navbar-text auto-refresh">
      <i class="fas fa-clock"></i> {{ now }} &nbsp;·&nbsp; Auto-refresh <span id="cd">30</span>s
    </span>
  </div>
</nav>

<div class="container-fluid mt-4">
  <div class="row">

<!-- SYSTEM STATUS -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-server section-icon"></i>System Status <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      <div class="row mb-2">
        <div class="col-6">ClawBot</div>
        <div class="col-6">
          {% if bot.running %}<span class="dot green"></span><span class="text-success">Active</span>
          {% else %}<span class="dot amber"></span><span class="text-warning">Idle</span>{% endif %}
          <small class="text-muted"> {{ bot.last_seen }}</small>
        </div>
      </div>
      <div class="row mb-2">
        <div class="col-6">Ollama</div>
        <div class="col-6">
          {% if ollama.online %}<span class="text-success"><i class="fas fa-check"></i> Online</span>
          {% else %}<span class="text-danger"><i class="fas fa-times"></i> Offline</span>{% endif %}
        </div>
      </div>
      {% if ollama.models %}
      <div class="row mb-2">
        <div class="col-6">Model</div>
        <div class="col-6"><small class="text-muted">{{ ollama.active }}</small>
      </div>
      {% endif %}
      <div class="row mb-2">
        <div class="col-6">Claude API</div>
        <div class="col-6">{% if claude_ok %}<span class="text-success"><i class="fas fa-check"></i> Configured</span>
        {% else %}<span class="text-warning"><i class="fas fa-exclamation-triangle"></i> Not set</span>{% endif %}</div>
      </div>
      <div class="row mb-2">
        <div class="col-6">Crypto.com</div>
        <div class="col-6">{% if crypto_ok %}<span class="text-success"><i class="fas fa-check"></i> Configured</span>
        {% else %}<span class="text-warning"><i class="fas fa-exclamation-triangle"></i> Not set</span>{% endif %}</div>
      </div>
      <div class="row">
        <div class="col-6">Cache</div>
        <div class="col-6">{{ cache.entries }} entries <small class="text-muted">{{ cache.newest }}</small></div>
      </div>
    </div>
  </div>
</div>

<!-- AUTO-TRADE STATUS -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-chart-line section-icon"></i>Auto-Trade <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if autotrade.enabled %}
      <div class="row mb-2">
        <div class="col-6">Status</div>
        <div class="col-6"><span class="badge bg-success">ENABLED</span></div>
      </div>
      <div class="row mb-2">
        <div class="col-6">Daily scan</div>
        <div class="col-6 text-success">{{ autotrade.scan_time }} UTC</div>
      </div>
      <div class="row mb-2">
        <div class="col-6">Timeframe</div>
        <div class="col-6">{{ autotrade.timeframe }}</div>
      </div>
      <div class="row mb-3">
        <div class="col-6">Strategy</div>
        <div class="col-6"><small class="text-muted">RSI+MACD · 1.5% risk</small></div>
      </div>
      {% else %}
      <div class="alert alert-warning">
        Auto-trade is <strong>disabled</strong>.<br>
        <small>Send /autotrade on in Telegram to enable.</small>
      </div>
      {% endif %}
      <div class="row">
        <div class="col-6">Total logged trades</div>
        <div class="col-6 {% if trades|length > 0 %}text-success{% else %}text-warning{% endif %}">{{ trades|length }}</div>
      </div>
    </div>
  </div>
</div>

<!-- LIVE PRICES -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100 price-card">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-coins section-icon"></i>Live Prices <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if prices %}
        {% for coin, d in prices.items() %}
        <div class="d-flex justify-content-between align-items-center mb-2">
          <span class="text-muted">{{ coin }}/USDT</span>
          <div>
            <span class="fw-bold">${{ "{:,.2f}".format(d.price) }}</span>
            <span class="ms-2 {{ 'text-success' if d.change >= 0 else 'text-danger' }}">{{ d.sign }}{{ d.change }}%</span>
          </div>
        </div>
        {% endfor %}
      {% else %}
        <div class="text-center text-muted">CoinGecko unavailable — refresh to retry</div>
      {% endif %}
    </div>
  </div>
</div>

<!-- BRAIN STATS -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-brain section-icon"></i>Brain Stats — Today <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      <div class="row mb-2">
        <div class="col-7">Ollama (free)</div>
        <div class="col-5 text-success">{{ usage.ollama_calls }} calls</div>
      </div>
      <div class="row mb-2">
        <div class="col-7">Claude Haiku</div>
        <div class="col-5 {% if usage.claude_calls > 0 %}text-warning{% endif %}">{{ usage.claude_calls }} calls</div>
      </div>
      <div class="row mb-2">
        <div class="col-7">Cache hits</div>
        <div class="col-5 text-success">{{ usage.cache_hits }} <i class="fas fa-database"></i></div>
      </div>
      <div class="row mb-2">
        <div class="col-7">Tokens in / out</div>
        <div class="col-5 text-muted">{{ "{:,}".format(usage.claude_input_tokens) }} / {{ "{:,}".format(usage.claude_output_tokens) }}</div>
      </div>
      <div class="row">
        <div class="col-7">API cost today</div>
        {% set cost = (usage.claude_input_tokens * 0.00000025) + (usage.claude_output_tokens * 0.00000125) %}
        <div class="col-5 {% if cost > 0.01 %}text-warning{% else %}text-success{% endif %}">${{ "%.4f"|format(cost) }}</div>
      </div>
    </div>
  </div>
</div>

<!-- BACKTEST RESULTS -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-chart-bar section-icon"></i>Backtest Results <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if backtest and backtest.ranking %}
        <div class="text-muted mb-3">{{ backtest.period_days // 365 }}Y history · generated {{ backtest.generated }}</div>
        <div class="table-responsive">
          <table class="table table-sm">
            <thead>
              <tr><th>#</th><th>Strategy / Pair</th><th>Return</th><th>Win%</th></tr>
            </thead>
            <tbody>
              {% for r in backtest.ranking %}
              <tr>
                <td class="text-muted">{{ loop.index }}</td>
                <td><span>{{ r.strategy }}</span><br><small class="text-muted">{{ r.pair }}</small></td>
                <td class="{% if r.total_return_pct > 0 %}text-success{% else %}text-danger{% endif %}">
                  {{ "%+.0f"|format(r.total_return_pct) }}%</td>
                <td class="text-muted">{{ r.win_rate }}%</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      {% else %}
        <div class="text-center text-muted">No backtest data yet.<br><small>Run /backtest run in Telegram.</small></div>
      {% endif %}
    </div>
  </div>
</div>

<!-- KNOWLEDGE NOTES -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-book section-icon"></i>Knowledge Notes <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if notes.count > 0 %}
        <div class="text-muted mb-3">{{ notes.count }} saved notes</div>
        {% for n in notes.recent %}
        <div class="mb-3">
          <div class="fw-bold">{{ n.title[:60] }}{% if n.title|length > 60 %}…{% endif %}</div>
          <div class="text-muted small">
            {{ n.timestamp[:10] }}
            {% for tag in n.tags[:3 %}<span class="badge bg-info ms-1">#{{ tag }}</span>{% endfor %}
          </div>
        </div>
        {% endfor %}
      {% else %}
        <div class="text-center text-muted">No notes yet.<br><small>Use /save in Telegram after a good conversation.</small></div>
      {% endif %}
    </div>
  </div>
</div>

<!-- REMINDERS -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-bell section-icon"></i>Pending Reminders <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if tasks %}
        <div class="table-responsive">
          <table class="table table-sm">
            <thead>
              <tr><th>Time (UTC)</th><th>Reminder</th></tr>
            </thead>
            <tbody>
              {% for t in tasks %}
              <tr>
                <td class="text-success">{{ t.time }}</td>
                <td>{{ t.text }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      {% else %}
        <div class="text-center text-muted">No pending reminders.<br><small>Use /remind HH:MM text in Telegram.</small></div>
      {% endif %}
    </div>
  </div>
</div>

<!-- ORCHESTRATION TASKS -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-users-cog section-icon"></i>Orchestration Tasks <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if orchestration %}
        <div class="table-responsive">
          <table class="table table-sm">
            <thead>
              <tr><th>ID</th><th>Task</th><th>State</th><th>Assigned</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {% for t in orchestration %}
              <tr>
                <td><small class="text-muted font-monospace">{{ t.id[-8:] }}</small></td>
                <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis">{{ t.title }}</td>
                <td>
                  <span class="badge {% if t.state == 'done' %}bg-success{% elif t.state == 'in_progress' %}bg-warning{% elif t.state == 'review' %}bg-info{% else %}bg-secondary{% endif %}">
                    {{ t.state|replace('_', ' ') }}
                  </span>
                </td>
                <td><small class="text-muted">{{ t.assigned_to[:12] }}{% if t.assigned_to|length > 12 %}…{% endif %}</small></td>
                <td>
                  {% if t.state == 'pending' %}
                    <button class="btn btn-sm btn-outline-success" onclick="updateTask('{{ t.id }}', 'start')"><i class="fas fa-play"></i></button>
                  {% elif t.state == 'in_progress' %}
                    <button class="btn btn-sm btn-outline-primary" onclick="updateTask('{{ t.id }}', 'review')"><i class="fas fa-check"></i></button>
                  {% elif t.state == 'review' %}
                    <button class="btn btn-sm btn-outline-success" onclick="updateTask('{{ t.id }}', 'done')"><i class="fas fa-check-double"></i></button>
                  {% endif %}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      {% else %}
        <div class="text-center text-muted">No orchestration tasks yet.<br><small>Use /orchestrate create in Telegram.</small></div>
      {% endif %}
    </div>
  </div>
</div>

<!-- CODE REVIEW -->
<div class="col-md-6 col-lg-4 mb-4">
  <div class="card h-100">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-code section-icon"></i>Last Code Review <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if codereview.date %}
        <div class="row mb-2">
          <div class="col-4">Date</div>
          <div class="col-8 text-success">{{ codereview.date }}</div>
        </div>
        <div class="text-muted small">{{ codereview.preview[:250] }}{% if codereview.preview|length > 250 %}…{% endif %}</div>
      {% else %}
        <div class="text-center text-muted">No code reviews yet.<br><small>Run /codereview run in Telegram.</small></div>
      {% endif %}
    </div>
  </div>
</div>

<!-- RECENT TRADES (full width) -->
<div class="col-12 mb-4">
  <div class="card">
    <div class="card-header collapsible" onclick="toggleCard(this)">
      <h5 class="mb-0"><i class="fas fa-history section-icon"></i>Recent Trade Log <i class="fas fa-chevron-down float-end"></i></h5>
    </div>
    <div class="card-body">
      {% if trades %}
        <div class="table-responsive">
          <table class="table table-sm">
            <thead>
              <tr><th>Time</th><th>Coin</th><th>Action</th><th>Conf</th><th>USD</th><th>Status</th><th>Notes</th></tr>
            </thead>
            <tbody>
              {% for t in trades|reverse %}
              <tr>
                <td class="text-muted small">{{ t.get('timestamp','')[:16]|replace('T',' ') }}</td>
                <td>{{ t.get('coin', t.get('action','?')) }}</td>
                <td>
                  {% set act = t.get('action','') %}
                  <span class="badge {% if act == 'BUY' %}bg-success{% elif act == 'SELL' %}bg-danger{% else %}bg-warning{% endif %}">
                    {{ act or '—' }}
                  </span>
                </td>
                <td class="text-muted">{{ t.get('confidence','—') }}</td>
                <td class="font-monospace">${{ "%.2f"|format(t.get('usd_amount',0)|float) }}</td>
                <td>
                  {% set st = t.get('status','') %}
                  <span class="badge {% if st == 'executed' %}bg-success{% elif st == 'error' %}bg-danger{% else %}bg-warning{% endif %}">
                    {{ st or '—' }}
                  </span>
                </td>
                <td class="text-muted small">{{ t.get('reason', t.get('notes',''))[:60] }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      {% else %}
        <div class="text-center text-muted">No trades logged yet. Enable /autotrade on in Telegram to start collecting data.</div>
      {% endif %}
    </div>
  </div>
</div>

  </div><!-- /row -->
</div><!-- /container -->

<!-- CHAT PANEL -->
<div class="chat-panel d-flex flex-column" id="chat-panel">
  <!-- Header -->
  <div class="chat-header d-flex align-items-center justify-content-between p-3" onclick="toggleChat()">
    <span class="fw-bold text-success"><i class="fas fa-robot"></i> ClawBot Chat</span>
    <div class="d-flex gap-2 align-items-center">
      <span id="brain-badge" class="text-muted small font-monospace"></span>
      <button onclick="clearChat(event)" class="btn btn-sm btn-outline-secondary" title="Clear chat"><i class="fas fa-redo"></i></button>
      <span id="chat-toggle-icon"><i class="fas fa-chevron-up"></i></span>
    </div>
  </div>

  <!-- Messages -->
  <div id="chat-messages" class="chat-messages d-flex flex-column gap-2 p-3">
    <div class="msg-bot p-2 rounded">
      Hey Ronnie — what's on your mind? Ask me anything about OpenClaw, trading, or ideas.
    </div>
  </div>

  <!-- Input -->
  <div class="p-3 border-top">
    <div class="input-group">
      <input id="chat-input" type="text" class="form-control" placeholder="Ask ClawBot..." onkeydown="if(event.key==='Enter' && !event.shiftKey){sendChat();event.preventDefault();}">
      <button onclick="sendChat()" id="send-btn" class="btn btn-custom"><i class="fas fa-paper-plane"></i></button>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
  // ── Dashboard auto-refresh (pauses while chat is active) ───────────────────
  let t = 30;
  let chatActive = false;  // pause refresh while user is chatting
  const el = document.getElementById('cd');
  setInterval(() => {
    if (chatActive) { t = 30; el.textContent = t; return; }
    t--;
    if (t <= 0) location.reload();
    else el.textContent = t;
  }, 1000);

  // ── Collapsible cards ──────────────────────────────────────────────────────
  function toggleCard(header) {
    const body = header.nextElementSibling;
    const icon = header.querySelector('.fa-chevron-down, .fa-chevron-up');
    if (body.style.display === 'none') {
      body.style.display = 'block';
      icon.classList.remove('fa-chevron-up');
      icon.classList.add('fa-chevron-down');
    } else {
      body.style.display = 'none';
      icon.classList.remove('fa-chevron-down');
      icon.classList.add('fa-chevron-up');
    }
  }

  // ── Chat panel ─────────────────────────────────────────────────────────────
  let chatOpen = true;
  const msgs   = document.getElementById('chat-messages');
  const input  = document.getElementById('chat-input');

  // Restore chat history from localStorage on page load
  const _CHAT_KEY = 'clawbot_chat_history';
  function _saveChat() {
    const items = [];
    msgs.querySelectorAll('div[data-role]').forEach(d => {
      items.push({role: d.dataset.role, text: d.dataset.text, brain: d.dataset.brain || ''});
    });
    localStorage.setItem(_CHAT_KEY, JSON.stringify(items.slice(-20)));
  }
  function _restoreChat() {
    try {
      const saved = JSON.parse(localStorage.getItem(_CHAT_KEY) || '[]');
      saved.forEach(m => _addMsgRaw(m.text, m.role, m.brain));
    } catch(e) {}
  }
  _restoreChat();

  function toggleChat() {
    chatOpen = !chatOpen;
    const panel = document.getElementById('chat-panel');
    const messages = panel.querySelector('.chat-messages');
    const inputDiv = panel.querySelector('.border-top');
    const icon = document.getElementById('chat-toggle-icon');
    
    if (chatOpen) {
      messages.style.display = 'flex';
      inputDiv.style.display = 'block';
      icon.innerHTML = '<i class="fas fa-chevron-up"></i>';
    } else {
      messages.style.display = 'none';
      inputDiv.style.display = 'none';
      icon.innerHTML = '<i class="fas fa-chevron-down"></i>';
    }
  }

  function _addMsgRaw(text, type, brain) {
    const div = document.createElement('div');
    div.className = `p-2 rounded ${type === 'user' ? 'msg-user' : 'msg-bot'}`;
    div.style.cssText = 'font-size: 0.9rem; line-height: 1.4; white-space: pre-wrap; word-break: break-word; max-width: 85%;';
    if (type === 'user') div.style.alignSelf = 'flex-end';
    div.dataset.role = type;
    div.dataset.text = text;
    div.dataset.brain = brain || '';
    div.textContent = text;
    if (brain && type === 'bot') {
      const badge = document.createElement('span');
      badge.textContent = ' (' + brain + ')';
      badge.className = 'text-muted small ms-1';
      div.appendChild(badge);
      document.getElementById('brain-badge').textContent = brain;
    }
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  }

  function addMsg(text, type, brain) {
    return _addMsgRaw(text, type, brain);
  }

  async function sendChat() {
    const text = input.value.trim();
    if (!text) return;

    input.value = '';
    input.disabled = true;
    document.getElementById('send-btn').disabled = true;
    chatActive = true;  // pause auto-refresh while waiting

    addMsg(text, 'user');
    _saveChat();
    const thinking = addMsg('...', 'bot');

    try {
      const res  = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text}),
      });
      const data = await res.json();
      if (data.error) {
        thinking.textContent = 'Error: ' + data.error;
        thinking.classList.add('text-danger');
        thinking.dataset.text = 'Error: ' + data.error;
      } else {
        thinking.textContent = data.reply;
        thinking.dataset.text = data.reply;
        thinking.dataset.brain = data.brain || '';
        const badge = document.createElement('span');
        badge.textContent = ' (' + (data.brain || '') + ')';
        badge.className = 'text-muted small ms-1';
        thinking.appendChild(badge);
        document.getElementById('brain-badge').textContent = data.brain || '';
      }
      _saveChat();
    } catch (e) {
      thinking.textContent = 'Connection error — is the dashboard running?';
      thinking.classList.add('text-danger');
    }
    input.disabled = false;
    document.getElementById('send-btn').disabled = false;
    chatActive = false;  // allow auto-refresh again
    input.focus();
  }

  async function clearChat(e) {
    e.stopPropagation();
    await fetch('/api/chat/clear', {method:'POST'});
    localStorage.removeItem(_CHAT_KEY);
    while (msgs.children.length > 1) msgs.removeChild(msgs.lastChild);
    document.getElementById('brain-badge').textContent = '';
  }

  // ── Task management ────────────────────────────────────────────────────────
  async function updateTask(taskId, action) {
    try {
      const res = await fetch('/api/task/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_id: taskId, action: action}),
      });
      const data = await res.json();
      if (data.success) {
        location.reload();
      } else {
        alert('Error updating task: ' + (data.error || 'Unknown error'));
      }
    } catch (e) {
      alert('Connection error: ' + e.message);
    }
  }

  input.focus();
</script>
</body>
</html>"""


# ── Chat API ───────────────────────────────────────────────────────────────────

# In-memory session history per browser session (keyed by a simple counter)
_web_history: list[dict] = []
_WEB_CHAT_ID = 0   # dashboard uses chat_id 0 (separate from Telegram)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """POST {"message": "..."} → {"reply": "...", "brain": "ollama|claude|cache"}"""
    global _web_history
    try:
        sys.path.insert(0, str(ROOT))
        from core.brain import ask_hybrid, CLAWBOT_SYSTEM

        data    = request.get_json(force=True)
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "Empty message"}), 400

        # Keep last 10 turns in memory
        _web_history.append({"role": "user", "content": message})
        _web_history = _web_history[-20:]

        reply, brain = ask_hybrid(
            message,
            system=CLAWBOT_SYSTEM,
            history=_web_history[:-1],   # history before this message
        )

        _web_history.append({"role": "assistant", "content": reply})
        _web_history = _web_history[-20:]

        return jsonify({"reply": reply, "brain": brain})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/chat/clear", methods=["POST"])
def api_chat_clear():
    global _web_history
    _web_history = []
    return jsonify({"ok": True})


@app.route("/api/task/update", methods=["POST"])
def api_task_update():
    """POST {"task_id": "...", "action": "start|review|done"} → {"success": true}"""
    try:
        from skills.agent_team_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        
        data = request.get_json(force=True)
        task_id = data.get("task_id")
        action = data.get("action")
        
        if not task_id or not action:
            return jsonify({"success": False, "error": "Missing task_id or action"}), 400
        
        task = orchestrator.tasks.get(task_id)
        if not task:
            return jsonify({"success": False, "error": "Task not found"}), 404
        
        if action == "start":
            task.start_task()
        elif action == "review":
            task.review_task()
        elif action == "done":
            task.complete_task()
        else:
            return jsonify({"success": False, "error": "Invalid action"}), 400
        
        orchestrator.save_tasks()
        return jsonify({"success": True})
    
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


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
    orchestration = get_orchestration_tasks()
    claude_ok  = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    crypto_ok  = bool(os.getenv("CRYPTOCOM_API_KEY", "").strip())
    now        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return render_template_string(
        DASHBOARD_HTML,
        usage=usage, prices=prices, ollama=ollama, bot=bot,
        tasks=tasks, trades=trades, cache=cache,
        autotrade=autotrade, backtest=backtest,
        notes=notes, codereview=codereview,
        orchestration=orchestration,
        claude_ok=claude_ok, crypto_ok=crypto_ok, now=now,
    )


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("OpenClaw Dashboard → http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
