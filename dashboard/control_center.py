"""
Local Control Center — institutional dashboard at :8082.

Shows: live portfolio, positions, fills, regime, AI decisions,
kill switch, latency, circuit breakers, replay, optimization jobs,
system health (CPU/RAM/GPU/Ollama).

Auth:   X-Auth-Token header (or ?token= query param) matching DASHBOARD_TOKEN env var.
        Default token is "changeme" — change in production.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request, Response

logger = logging.getLogger("openclaw.dashboard.control_center")

# ── Config ────────────────────────────────────────────────────────────────────

_DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "changeme")
_DATA_DIR = Path(__file__).parent.parent / "data"
_PORT = 8082

app = Flask(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ok(data: Any) -> Response:
    return jsonify({"ok": True, "data": data, "ts": _now_ms()})


def _err(msg: str, code: int = 400) -> Response:
    resp = jsonify({"ok": False, "error": msg, "ts": _now_ms()})
    resp.status_code = code
    return resp


def _read_json(filename: str, default: Any = None) -> Any:
    """Read a JSON file from the data directory; return default on any error."""
    if default is None:
        default = {}
    path = _DATA_DIR / filename
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as exc:
        logger.debug("Could not read %s: %s", path, exc)
    return default


def _write_json(filename: str, data: Any) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _DATA_DIR / filename
    path.write_text(json.dumps(data, indent=2))


def _check_auth() -> bool:
    token = request.headers.get("X-Auth-Token") or request.args.get("token", "")
    return token == _DASHBOARD_TOKEN


def _is_localhost() -> bool:
    host = request.remote_addr or ""
    return host in ("127.0.0.1", "::1", "localhost")


# ── System health (defensive imports) ─────────────────────────────────────────


def _get_system_health() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "cpu_pct": 0.0,
        "ram_pct": 0.0,
        "ram_used_gb": 0.0,
        "ram_free_gb": 0.0,
        "swap_used_gb": 0.0,
        "gpu": {"available": False},
        "thermal": {"cpu_temperature_c": 0.0, "gpu_temperature_c": 0.0},
        "is_healthy": True,
        "psutil_available": False,
    }

    try:
        import psutil  # type: ignore
        result["psutil_available"] = True
        vm = psutil.virtual_memory()
        result["cpu_pct"] = round(psutil.cpu_percent(interval=0.1), 2)
        result["ram_pct"] = round(vm.percent, 2)
        result["ram_used_gb"] = round(vm.used / (1024 ** 3), 3)
        result["ram_free_gb"] = round(vm.available / (1024 ** 3), 3)
        result["swap_used_gb"] = round(psutil.swap_memory().used / (1024 ** 3), 3)
        result["is_healthy"] = result["cpu_pct"] < 85.0 and vm.percent < 80.0

        # CPU temperature
        try:
            sensors = psutil.sensors_temperatures()
            if sensors:
                for key in ("coretemp", "k10temp", "cpu-thermal", "acpitz"):
                    if key in sensors and sensors[key]:
                        result["thermal"]["cpu_temperature_c"] = round(
                            sensors[key][0].current, 1
                        )
                        break
        except Exception:
            pass
    except ImportError:
        pass

    # GPU (nvidia-smi)
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            parts = [p.strip() for p in r.stdout.strip().split(",")]
            if len(parts) >= 4:
                util, mem_used, mem_total, temp = (float(p) for p in parts)
                result["gpu"] = {
                    "available": True,
                    "utilization_pct": round(util, 2),
                    "memory_used_mb": round(mem_used, 1),
                    "memory_total_mb": round(mem_total, 1),
                    "memory_free_mb": round(max(0.0, mem_total - mem_used), 1),
                    "temperature_c": round(temp, 1),
                }
                result["thermal"]["gpu_temperature_c"] = round(temp, 1)
    except Exception:
        pass

    return result


# ── HTML Dashboard ─────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenClaw Control Center</title>
<style>
  :root {
    --bg:      #0d0d0d;
    --surface: #161616;
    --border:  #2a2a2a;
    --accent:  #00ff88;
    --warn:    #ffaa00;
    --danger:  #ff3b3b;
    --text:    #e0e0e0;
    --muted:   #888;
    --radius:  6px;
    --font:    'Courier New', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
         font-size: 13px; line-height: 1.5; }
  header { display: flex; align-items: center; justify-content: space-between;
           padding: 12px 20px; background: var(--surface);
           border-bottom: 1px solid var(--border); }
  header h1 { color: var(--accent); font-size: 18px; letter-spacing: 2px; }
  #halt-badge { display: none; background: var(--danger); color: #fff;
                padding: 3px 10px; border-radius: 4px; font-size: 12px;
                animation: blink 1s step-start infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  #clock { color: var(--muted); font-size: 11px; }
  #localhost-warn { background: #2a1a00; border: 1px solid var(--warn);
                    color: var(--warn); padding: 8px 20px; font-size: 12px;
                    display: none; }
  main { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
         gap: 14px; padding: 16px; }
  section { background: var(--surface); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 14px; }
  section h2 { font-size: 11px; color: var(--accent); letter-spacing: 2px;
               text-transform: uppercase; margin-bottom: 10px;
               border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  .kv { display: flex; justify-content: space-between; padding: 3px 0;
        border-bottom: 1px solid #1c1c1c; }
  .kv:last-child { border-bottom: none; }
  .kv .k { color: var(--muted); }
  .kv .v { color: var(--text); font-weight: bold; }
  .v.ok  { color: var(--accent); }
  .v.warn { color: var(--warn); }
  .v.bad  { color: var(--danger); }
  .bar-wrap { background: #222; border-radius: 3px; height: 6px; margin: 4px 0 8px; }
  .bar { height: 6px; border-radius: 3px; background: var(--accent);
         transition: width .5s ease; }
  .bar.warn { background: var(--warn); }
  .bar.bad  { background: var(--danger); }
  table { width: 100%; border-collapse: collapse; margin-top: 4px; }
  th { text-align: left; color: var(--muted); font-weight: normal;
       font-size: 11px; padding: 4px 0; border-bottom: 1px solid var(--border); }
  td { padding: 4px 4px 4px 0; border-bottom: 1px solid #1a1a1a;
       font-size: 12px; }
  tr:last-child td { border-bottom: none; }
  .tag { display: inline-block; padding: 1px 6px; border-radius: 3px;
         font-size: 10px; font-weight: bold; }
  .tag-safe       { background: #00331a; color: var(--accent); }
  .tag-defensive  { background: #332200; color: var(--warn); }
  .tag-critical   { background: #330a0a; color: var(--danger); }
  .tag-halt       { background: var(--danger); color: #fff; }
  .tag-active     { background: #001a0d; color: var(--accent); }
  .tag-inactive   { background: #222; color: var(--muted); }
  .tag-pending    { background: #1a1a00; color: var(--warn); }
  .action-btn { background: #1a1a1a; border: 1px solid var(--border); color: var(--text);
                padding: 6px 14px; border-radius: 4px; cursor: pointer;
                font-family: var(--font); font-size: 12px; margin-top: 8px; }
  .action-btn:hover { border-color: var(--accent); color: var(--accent); }
  .action-btn.danger { border-color: var(--danger); color: var(--danger); }
  .action-btn.danger:hover { background: #2a0000; }
  #status-bar { padding: 4px 20px; font-size: 11px; color: var(--muted);
                background: var(--surface); border-top: 1px solid var(--border);
                position: fixed; bottom: 0; width: 100%; }
  .empty { color: var(--muted); font-style: italic; font-size: 12px; }
</style>
</head>
<body>

<div id="localhost-warn">
  Warning: This dashboard is intended for local access only.
  You appear to be accessing it from a remote address.
</div>

<header>
  <h1>OPENCLAW CONTROL CENTER</h1>
  <span id="halt-badge">GLOBAL HALT ACTIVE</span>
  <span id="clock">--</span>
</header>

<main>
  <!-- System Health -->
  <section id="sec-system">
    <h2>System Health</h2>
    <div id="system-content"><span class="empty">Loading…</span></div>
  </section>

  <!-- Portfolio -->
  <section id="sec-portfolio">
    <h2>Portfolio</h2>
    <div id="portfolio-content"><span class="empty">Loading…</span></div>
  </section>

  <!-- Strategies -->
  <section id="sec-strategies">
    <h2>Strategies</h2>
    <div id="strategies-content"><span class="empty">Loading…</span></div>
  </section>

  <!-- Risk State -->
  <section id="sec-risk">
    <h2>Risk State</h2>
    <div id="risk-content"><span class="empty">Loading…</span></div>
  </section>

  <!-- Governance -->
  <section id="sec-governance">
    <h2>Governance</h2>
    <div id="governance-content"><span class="empty">Loading…</span></div>
  </section>

  <!-- Regimes -->
  <section id="sec-regimes">
    <h2>Market Regimes</h2>
    <div id="regimes-content"><span class="empty">Loading…</span></div>
  </section>

  <!-- Kill Switch -->
  <section id="sec-kill">
    <h2>Kill Switch</h2>
    <div id="kill-content">
      <p style="color:var(--muted);font-size:12px;margin-bottom:8px;">
        Send emergency halt or release halt. Requires auth token.
      </p>
      <input id="kill-token" type="password" placeholder="Auth token"
             style="width:100%;padding:5px;background:#111;border:1px solid var(--border);
                    color:var(--text);border-radius:4px;font-family:var(--font);
                    font-size:12px;margin-bottom:8px;">
      <input id="kill-reason" type="text" placeholder="Reason (for release)"
             style="width:100%;padding:5px;background:#111;border:1px solid var(--border);
                    color:var(--text);border-radius:4px;font-family:var(--font);
                    font-size:12px;margin-bottom:8px;">
      <button class="action-btn danger" onclick="doHalt()">EMERGENCY HALT</button>
      <button class="action-btn" onclick="doRelease()" style="margin-left:8px;">
        RELEASE HALT
      </button>
      <div id="kill-msg" style="margin-top:8px;font-size:12px;color:var(--muted);"></div>
    </div>
  </section>
</main>

<div id="status-bar">Last update: <span id="last-ts">—</span> &nbsp;|&nbsp; Polling every 3s</div>

<script>
const TOKEN = new URLSearchParams(window.location.search).get('token') || '';

function barClass(pct) {
  if (pct > 85) return 'bad';
  if (pct > 65) return 'warn';
  return '';
}

function kv(k, v, cls='') {
  return `<div class="kv"><span class="k">${k}</span><span class="v ${cls}">${v}</span></div>`;
}

function pbar(pct) {
  const cls = barClass(pct);
  return `<div class="bar-wrap"><div class="bar ${cls}" style="width:${Math.min(pct,100)}%"></div></div>`;
}

function stateTag(s) {
  if (!s) return '';
  const cls = {
    'SAFE': 'tag-safe', 'DEFENSIVE': 'tag-defensive',
    'CRITICAL': 'tag-critical', 'EMERGENCY_HALT': 'tag-halt',
    'HALTED': 'tag-halt', 'ACTIVE': 'tag-active', 'INACTIVE': 'tag-inactive',
    'PENDING': 'tag-pending'
  }[s.toUpperCase()] || 'tag-inactive';
  return `<span class="tag ${cls}">${s}</span>`;
}

async function fetchApi(path) {
  try {
    const headers = {};
    if (TOKEN) headers['X-Auth-Token'] = TOKEN;
    const r = await fetch(path, { headers });
    return await r.json();
  } catch (e) {
    return { ok: false, data: {} };
  }
}

function renderSystem(d) {
  if (!d || Object.keys(d).length === 0) return '<span class="empty">No data</span>';
  const cpu = d.cpu_pct || 0;
  const ram = d.ram_pct || 0;
  const hcls = d.is_healthy ? 'ok' : 'bad';
  const gpu = d.gpu || {};
  let html = '';
  html += kv('Status', d.is_healthy ? 'Healthy' : 'Degraded', hcls);
  html += kv('CPU', cpu.toFixed(1) + '%', barClass(cpu));
  html += pbar(cpu);
  html += kv('RAM', ram.toFixed(1) + '%', barClass(ram));
  html += pbar(ram);
  html += kv('RAM Used', (d.ram_used_gb || 0).toFixed(2) + ' GB');
  html += kv('RAM Free', (d.ram_free_gb || 0).toFixed(2) + ' GB');
  html += kv('Swap', (d.swap_used_gb || 0).toFixed(2) + ' GB');
  if (gpu.available) {
    const gu = gpu.utilization_pct || 0;
    html += kv('GPU Util', gu.toFixed(1) + '%', barClass(gu));
    html += pbar(gu);
    html += kv('GPU Temp', (gpu.temperature_c || 0).toFixed(1) + '°C',
               gpu.temperature_c > 75 ? 'warn' : '');
    html += kv('GPU VRAM', `${(gpu.memory_used_mb/1024).toFixed(1)}/${(gpu.memory_total_mb/1024).toFixed(1)} GB`);
  } else {
    html += kv('GPU', 'Not available', 'muted');
  }
  const th = d.thermal || {};
  if (th.cpu_temperature_c) {
    html += kv('CPU Temp', th.cpu_temperature_c.toFixed(1) + '°C',
               th.cpu_temperature_c > 80 ? 'warn' : '');
  }
  return html;
}

function renderPortfolio(d) {
  if (!d || Object.keys(d).length === 0) return '<span class="empty">No portfolio data</span>';
  let html = '';
  html += kv('Balance', '$' + (d.balance || 0).toFixed(2));
  html += kv('Total PnL', '$' + (d.total_pnl || 0).toFixed(2),
             (d.total_pnl || 0) >= 0 ? 'ok' : 'bad');
  html += kv('Trades Today', d.trades_today || 0);
  html += kv('Open Positions', (d.open_positions || []).length);
  if (d.demo_mode !== undefined) {
    html += kv('Mode', d.demo_mode ? 'DEMO' : 'LIVE', d.demo_mode ? 'warn' : 'ok');
  }
  const pos = d.open_positions || [];
  if (pos.length > 0) {
    html += '<table style="margin-top:8px"><thead><tr><th>Symbol</th><th>Side</th><th>PnL</th></tr></thead><tbody>';
    for (const p of pos) {
      html += `<tr><td>${p.symbol||''}</td><td>${p.side||''}</td><td>${(p.pnl||0).toFixed(2)}</td></tr>`;
    }
    html += '</tbody></table>';
  }
  return html;
}

function renderStrategies(d) {
  if (!d || Object.keys(d).length === 0) return '<span class="empty">No strategy data</span>';
  const strategies = typeof d === 'object' && !Array.isArray(d) ? d : {};
  let html = '<table><thead><tr><th>Strategy</th><th>W</th><th>L</th><th>Wt</th></tr></thead><tbody>';
  for (const [name, info] of Object.entries(strategies)) {
    const w = info.wins || 0, l = info.losses || 0, wt = (info.weight || 1).toFixed(2);
    html += `<tr><td>${name}</td><td class="v ok">${w}</td><td class="v bad">${l}</td><td>${wt}</td></tr>`;
  }
  html += '</tbody></table>';
  return html;
}

function renderRisk(d) {
  if (!d || Object.keys(d).length === 0) return '<span class="empty">No risk data</span>';
  let html = '';
  html += kv('Capital State', stateTag(d.state || 'SAFE'));
  html += kv('Risk Scalar', (d.risk_scalar || 1.0).toFixed(2));
  html += kv('Should Flatten', d.should_flatten ? '<span class="v bad">YES</span>' : 'No');
  html += kv('Daily DD', (d.daily_drawdown_pct || 0).toFixed(2) + '%',
             (d.daily_drawdown_pct || 0) > 4 ? 'warn' : '');
  html += kv('Weekly DD', (d.weekly_drawdown_pct || 0).toFixed(2) + '%',
             (d.weekly_drawdown_pct || 0) > 8 ? 'warn' : '');
  html += kv('Loss Streak', d.loss_streak || 0,
             (d.loss_streak || 0) >= 4 ? 'warn' : '');
  html += kv('Weekend Mode', d.weekend_risk_active ? 'Active' : 'Off');
  return html;
}

function renderGovernance(d) {
  if (!d || Object.keys(d).length === 0) return '<span class="empty">No governance data</span>';
  let html = '';
  const pending = d.pending_approvals || [];
  html += kv('Halt Active', d.halt_active ? '<span class="v bad">YES</span>' : 'No');
  html += kv('Pending Approvals', pending.length);
  if (pending.length > 0) {
    html += '<table style="margin-top:8px"><thead><tr><th>ID</th><th>Type</th><th>Status</th></tr></thead><tbody>';
    for (const ap of pending.slice(0, 5)) {
      html += `<tr><td>${(ap.id||'').slice(0,8)}</td><td>${ap.type||''}</td><td>${stateTag(ap.status||'PENDING')}</td></tr>`;
    }
    html += '</tbody></table>';
  }
  const decisions = d.recent_decisions || [];
  if (decisions.length > 0) {
    html += '<div style="margin-top:8px;font-size:11px;color:var(--muted)">Recent decisions:</div>';
    for (const dec of decisions.slice(0, 3)) {
      html += `<div style="font-size:11px;padding:2px 0;border-bottom:1px solid #1a1a1a">${dec}</div>`;
    }
  }
  return html;
}

function renderRegimes(d) {
  if (!d || Object.keys(d).length === 0) return '<span class="empty">No regime data</span>';
  let html = '<table><thead><tr><th>Symbol</th><th>Regime</th><th>Conf</th></tr></thead><tbody>';
  for (const [sym, info] of Object.entries(d)) {
    const regime = typeof info === 'string' ? info : (info.regime || info.label || '?');
    const conf   = typeof info === 'object' ? (info.confidence || info.conf || '') : '';
    const confStr = conf ? (parseFloat(conf)*100).toFixed(0)+'%' : '—';
    html += `<tr><td>${sym}</td><td>${stateTag(regime)}</td><td>${confStr}</td></tr>`;
  }
  html += '</tbody></table>';
  return html;
}

function setHaltBadge(active) {
  const el = document.getElementById('halt-badge');
  el.style.display = active ? 'inline-block' : 'none';
}

let globalHaltActive = false;

async function refresh() {
  const [sys, port, strats, risk, gov, regimes] = await Promise.all([
    fetchApi('/api/system'),
    fetchApi('/api/portfolio'),
    fetchApi('/api/strategies'),
    fetchApi('/api/risk'),
    fetchApi('/api/governance'),
    fetchApi('/api/regimes'),
  ]);

  document.getElementById('system-content').innerHTML     = renderSystem(sys.data || {});
  document.getElementById('portfolio-content').innerHTML  = renderPortfolio(port.data || {});
  document.getElementById('strategies-content').innerHTML = renderStrategies(strats.data || {});
  document.getElementById('risk-content').innerHTML       = renderRisk(risk.data || {});
  document.getElementById('governance-content').innerHTML = renderGovernance(gov.data || {});
  document.getElementById('regimes-content').innerHTML    = renderRegimes(regimes.data || {});

  globalHaltActive = (gov.data || {}).halt_active || false;
  setHaltBadge(globalHaltActive);

  document.getElementById('last-ts').textContent = new Date().toLocaleTimeString();
}

async function doHalt() {
  const token = document.getElementById('kill-token').value;
  const msg   = document.getElementById('kill-msg');
  msg.textContent = 'Sending halt…';
  try {
    const r = await fetch('/api/halt', {
      method: 'POST',
      headers: { 'X-Auth-Token': token, 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const j = await r.json();
    msg.textContent = j.ok ? 'Halt sent.' : ('Error: ' + (j.error || '?'));
    msg.style.color = j.ok ? 'var(--warn)' : 'var(--danger)';
    if (j.ok) refresh();
  } catch (e) {
    msg.textContent = 'Request failed.';
    msg.style.color = 'var(--danger)';
  }
}

async function doRelease() {
  const token  = document.getElementById('kill-token').value;
  const reason = document.getElementById('kill-reason').value;
  const msg    = document.getElementById('kill-msg');
  msg.textContent = 'Sending release…';
  try {
    const r = await fetch('/api/release_halt', {
      method: 'POST',
      headers: { 'X-Auth-Token': token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    const j = await r.json();
    msg.textContent = j.ok ? 'Halt released.' : ('Error: ' + (j.error || '?'));
    msg.style.color = j.ok ? 'var(--accent)' : 'var(--danger)';
    if (j.ok) refresh();
  } catch (e) {
    msg.textContent = 'Request failed.';
    msg.style.color = 'var(--danger)';
  }
}

function tick() {
  document.getElementById('clock').textContent = new Date().toUTCString().slice(17, 25) + ' UTC';
}

// Check if remote and show warning
if (window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
  document.getElementById('localhost-warn').style.display = 'block';
}

refresh();
setInterval(refresh, 3000);
setInterval(tick, 1000);
tick();
</script>
</body>
</html>"""


# ── Routes — public ────────────────────────────────────────────────────────────


@app.route("/")
def index() -> Response:
    # Warn in response header if not localhost
    resp = Response(_HTML, mimetype="text/html")
    if not _is_localhost():
        resp.headers["X-Local-Warning"] = "Non-localhost access detected"
    return resp


# ── Routes — API (read-only, no auth required for reads) ──────────────────────


@app.route("/api/system")
def api_system() -> Response:
    return _ok(_get_system_health())


@app.route("/api/portfolio")
def api_portfolio() -> Response:
    data = _read_json("sim_state.json", {})
    return _ok(data)


@app.route("/api/strategies")
def api_strategies() -> Response:
    data = _read_json("strategy_weights.json", {})
    return _ok(data)


@app.route("/api/regimes")
def api_regimes() -> Response:
    data = _read_json("regimes.json", {})
    return _ok(data)


@app.route("/api/governance")
def api_governance() -> Response:
    data = _read_json("governance.json", {})

    # Also try to read halt flag from halt state file
    halt_state = _read_json("halt_state.json", {})
    data["halt_active"] = halt_state.get("halt_active", False)

    return _ok(data)


@app.route("/api/lifecycle")
def api_lifecycle() -> Response:
    data = _read_json("lifecycle.json", {})
    return _ok(data)


@app.route("/api/risk")
def api_risk() -> Response:
    data = _read_json("capital_state.json", {})
    return _ok(data)


@app.route("/api/metrics_summary")
def api_metrics_summary() -> Response:
    summary: Dict[str, Any] = {}
    try:
        from prometheus_client import REGISTRY  # type: ignore
        for metric in REGISTRY.collect():
            for sample in metric.samples:
                summary[sample.name] = sample.value
    except Exception:
        pass
    return _ok(summary)


# ── Routes — mutating (require auth) ──────────────────────────────────────────


@app.route("/api/halt", methods=["POST"])
def api_halt() -> Response:
    if not _check_auth():
        return _err("Unauthorized — X-Auth-Token required", 401)

    halt_state = _read_json("halt_state.json", {})
    halt_state["halt_active"] = True
    halt_state["halt_ts"] = _now_ms()
    halt_state["halt_reason"] = "Manual halt via Control Center"

    try:
        _write_json("halt_state.json", halt_state)
    except Exception as exc:
        return _err(f"Could not write halt state: {exc}", 500)

    logger.warning("GLOBAL HALT activated via Control Center dashboard")
    return _ok({"halted": True})


@app.route("/api/release_halt", methods=["POST"])
def api_release_halt() -> Response:
    if not _check_auth():
        return _err("Unauthorized — X-Auth-Token required", 401)

    body: Dict[str, Any] = {}
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        pass

    reason = body.get("reason", "").strip()
    if not reason:
        return _err("reason is required to release halt", 400)

    halt_state = _read_json("halt_state.json", {})
    halt_state["halt_active"] = False
    halt_state["release_ts"] = _now_ms()
    halt_state["release_reason"] = reason

    try:
        _write_json("halt_state.json", halt_state)
    except Exception as exc:
        return _err(f"Could not write halt state: {exc}", 500)

    logger.warning("GLOBAL HALT released via Control Center dashboard — reason: %s", reason)
    return _ok({"halted": False, "reason": reason})


# ── Entry point ────────────────────────────────────────────────────────────────


def create_app() -> Flask:
    """Factory function for WSGI servers."""
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting OpenClaw Control Center on :%d", _PORT)
    app.run(host="0.0.0.0", port=_PORT, debug=False, threaded=True)
