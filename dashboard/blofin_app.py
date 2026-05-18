"""BloFin Adaptive Trading Bot Dashboard — http://localhost:8081

Live monitoring for all four strategies, open positions, trade log,
and self-learning weight progression. Polls /api/status every 4 seconds.

Usage:
    python dashboard/blofin_app.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

sys.path.insert(0, str(ROOT))

from trading.blofin_bot import BloFinBot

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s")
logger = logging.getLogger("blofin.dashboard")

app = Flask(__name__)
_bot = BloFinBot()


# ── Price cache (avoid rate-limits) ──────────────────────────────────────────
_price_cache: dict = {"ts": 0, "data": {}}


def _live_prices() -> dict:
    global _price_cache
    if time.time() - _price_cache["ts"] < 15:
        return _price_cache["data"]
    try:
        import requests as req
        r = req.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true",
            timeout=6,
        )
        if r.status_code == 200:
            raw  = r.json()
            data = {}
            for coin, cid in [("BTC", "bitcoin"), ("ETH", "ethereum"), ("SOL", "solana")]:
                d   = raw.get(cid, {})
                chg = d.get("usd_24h_change", 0) or 0
                data[coin] = {
                    "price":  d.get("usd", 0),
                    "change": round(chg, 2),
                    "up":     chg >= 0,
                }
            _price_cache = {"ts": time.time(), "data": data}
            return data
    except Exception:
        pass
    return _price_cache.get("data", {})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/status")
def api_status():
    status = _bot.get_status()
    status["prices"] = _live_prices()
    return jsonify(status)


@app.route("/api/start", methods=["POST"])
def api_start():
    _bot.start()
    return jsonify({"ok": True, "msg": "Bot started"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    _bot.stop()
    return jsonify({"ok": True, "msg": "Bot stopped"})


@app.route("/api/configure", methods=["POST"])
def api_configure():
    body     = request.get_json(force=True, silent=True) or {}
    demo     = body.get("demo_mode")
    risk     = body.get("risk_pct")
    _bot.configure(
        demo_mode=bool(demo) if demo is not None else None,
        risk_pct=float(risk) if risk is not None else None,
    )
    return jsonify({"ok": True})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    body       = request.get_json(force=True, silent=True) or {}
    api_key    = body.get("api_key",    "").strip()
    secret     = body.get("secret",     "").strip()
    passphrase = body.get("passphrase", "").strip()

    if api_key:
        os.environ["BLOFIN_API_KEY"]    = api_key
        os.environ["BLOFIN_SECRET"]     = secret
        os.environ["BLOFIN_PASSPHRASE"] = passphrase

        from trading.blofin_exchange import test_connection
        result = test_connection()
        return jsonify(result)

    return jsonify({"ok": False, "msg": "No API key provided"})


# ── HTML + CSS + JS dashboard ─────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BloFin Adaptive Bot</title>
<style>
/* ── Reset & base ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #080b10;
  color: #c8d0d8;
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  padding: 0 0 40px;
}
a { color: #00e5a0; text-decoration: none; }

/* ── Header ── */
.header {
  background: linear-gradient(135deg, #0d1117 0%, #0f1823 100%);
  border-bottom: 1px solid #1c2a3a;
  padding: 18px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.header-left { display: flex; align-items: center; gap: 14px; }
.header h1 { font-size: 1.3rem; font-weight: 700; color: #e8f0f8; letter-spacing: -0.3px; }
.header h1 span { color: #00e5a0; }
.badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; border-radius: 20px; font-size: 0.72rem; font-weight: 600;
  letter-spacing: 0.5px; text-transform: uppercase;
}
.badge-live  { background: #00e5a01a; color: #00e5a0; border: 1px solid #00e5a030; }
.badge-demo  { background: #f59e0b1a; color: #f59e0b; border: 1px solid #f59e0b30; }
.badge-run   { background: #22c55e1a; color: #22c55e; border: 1px solid #22c55e30; }
.badge-stop  { background: #6b728020; color: #6b7280; border: 1px solid #6b728030; }
.scan-info   { font-size: 0.78rem; color: #4b5563; }
.scan-info span { color: #6b7280; }

/* ── Layout ── */
.page { padding: 20px 28px; }
.grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 18px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 18px; }
@media (max-width: 900px) {
  .grid-4 { grid-template-columns: repeat(2, 1fr); }
  .grid-2 { grid-template-columns: 1fr; }
  .grid-3 { grid-template-columns: 1fr; }
}

/* ── Cards ── */
.card {
  background: #0e1420;
  border: 1px solid #1c2a3a;
  border-radius: 12px;
  padding: 18px 20px;
}
.card-title {
  font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 1px; color: #3d5268; margin-bottom: 12px;
}

/* ── Stat cards ── */
.stat-value  { font-size: 1.65rem; font-weight: 700; font-family: monospace; color: #e2e8f0; }
.stat-label  { font-size: 0.76rem; color: #4b5563; margin-top: 3px; }
.stat-sub    { font-size: 0.82rem; font-family: monospace; margin-top: 4px; }

/* ── Colors ── */
.green  { color: #00e5a0; }
.red    { color: #f87171; }
.amber  { color: #f59e0b; }
.muted  { color: #4b5563; }
.white  { color: #e2e8f0; }

/* ── Strategy cards ── */
.strat-card { padding: 14px 16px; }
.strat-name { font-size: 0.8rem; font-weight: 700; color: #94a3b8; margin-bottom: 8px; }
.strat-stats { display: flex; gap: 16px; font-size: 0.76rem; color: #6b7280; margin-bottom: 10px; }
.strat-stats b { color: #94a3b8; }
.weight-bar-bg {
  height: 5px; background: #1c2a3a; border-radius: 3px; overflow: hidden;
}
.weight-bar-fill {
  height: 100%; border-radius: 3px;
  background: linear-gradient(90deg, #3b82f6, #00e5a0);
  transition: width 0.6s ease;
}
.weight-label {
  display: flex; justify-content: space-between;
  font-size: 0.72rem; color: #4b5563; margin-top: 5px;
}

/* ── Controls ── */
.controls-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 600px) { .controls-grid { grid-template-columns: 1fr; } }
.ctrl-row { margin-bottom: 12px; }
.ctrl-label { font-size: 0.76rem; color: #6b7280; margin-bottom: 6px; }
.ctrl-label b { color: #94a3b8; }
input[type=range] {
  width: 100%; accent-color: #00e5a0; cursor: pointer;
}
input[type=text], input[type=password] {
  width: 100%; background: #080b10; border: 1px solid #1c2a3a;
  color: #c8d0d8; border-radius: 6px; padding: 7px 10px;
  font-size: 0.83rem; outline: none; margin-bottom: 6px;
}
input[type=text]:focus, input[type=password]:focus {
  border-color: #00e5a040;
}
.toggle-row { display: flex; align-items: center; gap: 10px; }
.toggle {
  position: relative; width: 44px; height: 24px; cursor: pointer;
}
.toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
.toggle-slider {
  position: absolute; inset: 0; background: #1c2a3a; border-radius: 24px;
  transition: background 0.25s;
}
.toggle-slider::before {
  content: ''; position: absolute; width: 18px; height: 18px;
  left: 3px; top: 3px; background: #4b5563; border-radius: 50%;
  transition: transform 0.25s, background 0.25s;
}
.toggle input:checked + .toggle-slider { background: #00e5a020; border: 1px solid #00e5a040; }
.toggle input:checked + .toggle-slider::before { transform: translateX(20px); background: #00e5a0; }

/* ── Buttons ── */
.btn {
  display: inline-flex; align-items: center; justify-content: center;
  gap: 6px; padding: 8px 18px; border-radius: 8px; font-size: 0.83rem;
  font-weight: 600; border: none; cursor: pointer; transition: all 0.15s;
}
.btn-start {
  background: linear-gradient(135deg, #065f46, #047857);
  color: #a7f3d0; border: 1px solid #064e3b;
}
.btn-start:hover { background: linear-gradient(135deg, #047857, #059669); }
.btn-stop {
  background: #1c2a3a; color: #94a3b8; border: 1px solid #2d3f52;
}
.btn-stop:hover { background: #2d3f52; }
.btn-connect {
  background: #1e3a5f; color: #93c5fd; border: 1px solid #2563eb30;
  width: 100%; margin-top: 4px;
}
.btn-connect:hover { background: #2563eb30; }
.btn-row { display: flex; gap: 8px; margin-top: 8px; }

/* ── Prices ── */
.price-row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #1c2a3a; }
.price-row:last-child { border-bottom: none; }
.price-coin { font-size: 0.78rem; font-weight: 600; color: #64748b; }
.price-val  { font-size: 1.1rem; font-family: monospace; font-weight: 700; color: #e2e8f0; }
.price-chg  { font-size: 0.78rem; font-family: monospace; }

/* ── Tables ── */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
th { text-align: left; color: #3d5268; font-weight: 600; padding: 6px 10px; border-bottom: 1px solid #1c2a3a; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.6px; }
td { padding: 9px 10px; border-bottom: 1px solid #0e1420; vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #0f1825; }
.empty { color: #2d3f52; font-size: 0.82rem; padding: 16px 0; text-align: center; }
.pill {
  display: inline-block; padding: 2px 8px; border-radius: 12px;
  font-size: 0.7rem; font-weight: 600; letter-spacing: 0.3px;
}
.pill-long  { background: #00e5a015; color: #00e5a0; border: 1px solid #00e5a025; }
.pill-short { background: #f8717115; color: #f87171; border: 1px solid #f8717125; }
.pill-win   { background: #22c55e15; color: #22c55e; border: 1px solid #22c55e25; }
.pill-loss  { background: #ef444415; color: #ef4444; border: 1px solid #ef444425; }

/* ── Status bar ── */
.status-bar {
  background: #0a0f18; border-top: 1px solid #1c2a3a;
  padding: 8px 28px; font-size: 0.76rem; color: #3d5268;
  display: flex; justify-content: space-between;
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 10;
}
.status-msg { color: #4b5563; max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Connection status ── */
#connect-result { font-size: 0.78rem; margin-top: 6px; min-height: 16px; }

/* ── Dot pulse ── */
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; }
.dot-green { background: #00e5a0; box-shadow: 0 0 6px #00e5a070; animation: pulse 2s infinite; }
.dot-red   { background: #6b7280; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

/* ── Section heading ── */
.section-heading {
  font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 1px; color: #3d5268; margin: 4px 0 10px;
}
</style>
</head>
<body>

<!-- ── Header ─────────────────────────────────────────────────────────────── -->
<div class="header">
  <div class="header-left">
    <h1>⚡ <span>BloFin</span> Adaptive Bot</h1>
    <span id="badge-run"  class="badge badge-stop">⏹ Stopped</span>
    <span id="badge-mode" class="badge badge-demo">Demo</span>
  </div>
  <div class="scan-info">
    Last scan: <span id="last-scan">—</span>
    &nbsp;·&nbsp; Next in <span id="countdown">—</span>s
  </div>
</div>

<!-- ── Page ──────────────────────────────────────────────────────────────── -->
<div class="page">

  <!-- Stat row -->
  <div class="grid-4">
    <div class="card">
      <div class="card-title">Balance</div>
      <div class="stat-value green" id="s-balance">$—</div>
      <div class="stat-label">USDT available</div>
    </div>
    <div class="card">
      <div class="card-title">Total P&amp;L</div>
      <div class="stat-value" id="s-total-pnl">$—</div>
      <div class="stat-label">Realised all-time</div>
    </div>
    <div class="card">
      <div class="card-title">Open P&amp;L</div>
      <div class="stat-value" id="s-open-pnl">$—</div>
      <div class="stat-label">Unrealised</div>
    </div>
    <div class="card">
      <div class="card-title">Trades Today</div>
      <div class="stat-value white" id="s-trades">0</div>
      <div class="stat-label">Positions opened</div>
    </div>
  </div>

  <!-- Strategies + Controls -->
  <div class="grid-2">

    <!-- Strategy weight grid -->
    <div class="card">
      <div class="section-heading">Strategy Weights &amp; Performance</div>
      <div id="strat-grid">
        <div class="empty">Loading…</div>
      </div>
    </div>

    <!-- Controls -->
    <div class="card">
      <div class="section-heading">Controls</div>
      <div class="controls-grid">

        <!-- Left col: bot controls -->
        <div>
          <div class="ctrl-row">
            <div class="ctrl-label">Mode</div>
            <div class="toggle-row">
              <label class="toggle">
                <input type="checkbox" id="demo-toggle" checked onchange="toggleDemo()">
                <span class="toggle-slider"></span>
              </label>
              <span id="mode-label" class="amber" style="font-size:0.82rem;font-weight:600">Demo Mode</span>
            </div>
          </div>

          <div class="ctrl-row">
            <div class="ctrl-label">
              Risk per trade:
              <b id="risk-val" style="color:#00e5a0">1.5%</b>
            </div>
            <input type="range" id="risk-slider" min="0.5" max="4.0" step="0.5" value="1.5"
                   oninput="updateRisk(this.value)" onchange="applyRisk(this.value)">
            <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:#3d5268;margin-top:2px">
              <span>0.5%</span><span>Conservative</span><span>4%</span>
            </div>
          </div>

          <div class="btn-row">
            <button class="btn btn-start" onclick="startBot()">▶ Start</button>
            <button class="btn btn-stop"  onclick="stopBot()">⏹ Stop</button>
          </div>
        </div>

        <!-- Right col: API connect -->
        <div>
          <div class="ctrl-row">
            <div class="ctrl-label"><b>API Key</b></div>
            <input type="text"     id="inp-key"   placeholder="BloFin API key">
            <div class="ctrl-label"><b>Secret</b></div>
            <input type="password" id="inp-secret" placeholder="API secret">
            <div class="ctrl-label"><b>Passphrase</b></div>
            <input type="password" id="inp-pass"   placeholder="API passphrase">
            <button class="btn btn-connect" onclick="connectAPI()">Connect Live</button>
            <div id="connect-result" class="muted">Enter credentials to go live.</div>
          </div>
        </div>

      </div>
    </div>
  </div>

  <!-- Live prices row -->
  <div class="grid-3" style="margin-bottom:18px">
    <div class="card" id="price-btc">
      <div class="card-title">BTC-USDT</div>
      <div class="price-val muted">Loading…</div>
    </div>
    <div class="card" id="price-eth">
      <div class="card-title">ETH-USDT</div>
      <div class="price-val muted">Loading…</div>
    </div>
    <div class="card" id="price-sol">
      <div class="card-title">SOL-USDT</div>
      <div class="price-val muted">Loading…</div>
    </div>
  </div>

  <!-- Open positions -->
  <div class="card" style="margin-bottom:18px">
    <div class="section-heading">Open Positions</div>
    <div class="table-wrap" id="positions-wrap">
      <div class="empty">No open positions.</div>
    </div>
  </div>

  <!-- Trade log -->
  <div class="card">
    <div class="section-heading">Recent Trades</div>
    <div class="table-wrap" id="trades-wrap">
      <div class="empty">No closed trades yet.</div>
    </div>
  </div>

</div>

<!-- ── Status bar ─────────────────────────────────────────────────────────── -->
<div class="status-bar">
  <span class="status-msg" id="status-msg">Idle</span>
  <span>BloFin Adaptive Bot &nbsp;·&nbsp; openclaw</span>
</div>


<script>
// ── State ─────────────────────────────────────────────────────────────────────
let _pollInterval = 4;
let _countdown    = _pollInterval;
let _timer        = null;

// ── Polling ───────────────────────────────────────────────────────────────────
async function poll() {
  try {
    const r    = await fetch('/api/status');
    const data = await r.json();
    render(data);
  } catch(e) {
    document.getElementById('status-msg').textContent = 'Connection error — retrying…';
  }
}

function startPolling() {
  poll();
  clearInterval(_timer);
  _timer = setInterval(() => {
    _countdown--;
    const el = document.getElementById('countdown');
    if (el) el.textContent = _countdown;
    if (_countdown <= 0) {
      _countdown = _pollInterval;
      poll();
    }
  }, 1000);
}

// ── Render ────────────────────────────────────────────────────────────────────
function fmt(n) {
  const s = Math.abs(n).toFixed(2);
  return (n >= 0 ? '+' : '-') + '$' + parseFloat(s).toLocaleString('en-US', {minimumFractionDigits:2});
}
function cls(n)  { return n >= 0 ? 'green' : 'red'; }
function sign(n) { return n >= 0 ? '+' : ''; }

function render(d) {
  // Header badges
  const runBadge  = document.getElementById('badge-run');
  const modeBadge = document.getElementById('badge-mode');
  if (d.running) {
    runBadge.className  = 'badge badge-run';
    runBadge.innerHTML  = '<span class="dot dot-green"></span> Running';
  } else {
    runBadge.className  = 'badge badge-stop';
    runBadge.innerHTML  = '⏹ Stopped';
  }
  modeBadge.className   = d.demo_mode ? 'badge badge-demo' : 'badge badge-live';
  modeBadge.textContent = d.demo_mode ? 'Demo' : 'Live';

  document.getElementById('last-scan').textContent = d.last_scan || '—';

  // Stats
  setEl('s-balance', '$' + Number(d.balance).toLocaleString('en-US', {minimumFractionDigits:2}));
  setElCls('s-total-pnl', fmt(d.total_pnl), cls(d.total_pnl));
  setElCls('s-open-pnl',  fmt(d.unrealized_pnl), cls(d.unrealized_pnl));
  setEl('s-trades', d.trades_today);

  // Status
  document.getElementById('status-msg').textContent = d.status_msg || '';

  // Controls sync
  const slider = document.getElementById('risk-slider');
  if (slider && !slider.matches(':active')) {
    slider.value = d.risk_pct;
    document.getElementById('risk-val').textContent = d.risk_pct + '%';
  }
  const toggle = document.getElementById('demo-toggle');
  if (toggle && !toggle.matches(':active')) {
    toggle.checked = d.demo_mode;
    renderModeLabel(d.demo_mode);
  }

  // Strategies
  renderStrategies(d.strategy_weights);

  // Prices
  renderPrices(d.prices || {});

  // Positions
  renderPositions(d.open_positions || []);

  // Trades
  renderTrades(d.trade_log || []);
}

function setEl(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}
function setElCls(id, html, cls) {
  const el = document.getElementById(id);
  if (el) { el.innerHTML = html; el.className = 'stat-value ' + cls; }
}

function renderModeLabel(demo) {
  const lbl = document.getElementById('mode-label');
  if (!lbl) return;
  lbl.textContent = demo ? 'Demo Mode' : 'Live Trading';
  lbl.className   = demo ? 'amber' : 'green';
  lbl.style.fontSize = '0.82rem';
  lbl.style.fontWeight = '600';
}

function renderStrategies(weights) {
  const names = {
    EMA_CROSS:       'EMA Cross 9/21',
    RSI_MEAN_REVERT: 'RSI Mean Revert',
    BREAKOUT:        'Breakout 20p',
    FUNDING_ARB:     'Funding Arb',
  };
  let html = '';
  for (const [key, s] of Object.entries(weights || {})) {
    const pct   = Math.round((s.weight / 2.0) * 100);
    const wr    = s.trades > 0 ? s.win_rate.toFixed(1) + '%' : '—';
    const wClr  = s.weight >= 1.2 ? '#00e5a0' : s.weight <= 0.5 ? '#f87171' : '#f59e0b';
    html += `
    <div style="margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <span class="strat-name">${names[key] || key}</span>
        <span style="font-size:0.72rem;color:${wClr};font-weight:700">${s.weight.toFixed(2)}×</span>
      </div>
      <div class="strat-stats">
        <span><b>${s.trades}</b> trades</span>
        <span><b class="${s.wins > s.losses ? 'green' : 'muted'}">${s.wins}W</b>/<b class="${s.losses > s.wins ? 'red' : 'muted'}">${s.losses}L</b></span>
        <span>WR <b>${wr}</b></span>
      </div>
      <div class="weight-bar-bg">
        <div class="weight-bar-fill" style="width:${pct}%"></div>
      </div>
      <div class="weight-label"><span>0.2×</span><span style="color:${wClr}">${s.weight.toFixed(2)}×</span><span>2.0×</span></div>
    </div>`;
  }
  document.getElementById('strat-grid').innerHTML = html || '<div class="empty">No strategy data.</div>';
}

function renderPrices(prices) {
  const ids = { BTC: 'price-btc', ETH: 'price-eth', SOL: 'price-sol' };
  for (const [coin, id] of Object.entries(ids)) {
    const el = document.getElementById(id);
    if (!el) continue;
    const p = prices[coin];
    if (!p) { el.querySelector('.price-val') && (el.querySelector('.price-val').textContent = '—'); continue; }
    const chgCls = p.up ? 'green' : 'red';
    const chgSign = p.up ? '+' : '';
    el.innerHTML = `
      <div class="card-title">${coin}-USDT</div>
      <div style="display:flex;align-items:baseline;gap:10px">
        <span class="price-val">$${Number(p.price).toLocaleString('en-US',{minimumFractionDigits:2})}</span>
        <span class="price-chg ${chgCls}">${chgSign}${p.change}% 24h</span>
      </div>`;
  }
}

function renderPositions(positions) {
  const wrap = document.getElementById('positions-wrap');
  if (!positions.length) {
    wrap.innerHTML = '<div class="empty">No open positions.</div>';
    return;
  }
  let rows = positions.map(p => {
    const pnlCls  = p.unrealized_pnl >= 0 ? 'green' : 'red';
    const pnlStr  = (p.unrealized_pnl >= 0 ? '+' : '') + p.unrealized_pnl.toFixed(4);
    const sidePill = p.side === 'long' ? 'pill-long' : 'pill-short';
    return `<tr>
      <td><b>${p.symbol}</b></td>
      <td><span class="pill ${sidePill}">${p.side.toUpperCase()}</span></td>
      <td style="color:#94a3b8;font-size:0.76rem">${p.strategy}</td>
      <td style="font-family:monospace">$${p.entry_price.toLocaleString('en-US',{minimumFractionDigits:2})}</td>
      <td style="font-family:monospace">$${p.current_price.toLocaleString('en-US',{minimumFractionDigits:2})}</td>
      <td class="${pnlCls}" style="font-family:monospace">${pnlStr}</td>
      <td style="color:#4b5563;font-size:0.75rem">SL $${p.sl_price.toFixed(2)}</td>
      <td style="color:#4b5563;font-size:0.75rem">TP $${p.tp_price.toFixed(2)}</td>
      <td style="color:#3d5268;font-size:0.75rem">${p.opened_at}</td>
      <td><span style="font-size:0.72rem;color:${p.confidence>=70?'#00e5a0':p.confidence>=50?'#f59e0b':'#f87171'}">${p.confidence}%</span></td>
    </tr>`;
  }).join('');
  wrap.innerHTML = `<table>
    <thead><tr>
      <th>Symbol</th><th>Side</th><th>Strategy</th>
      <th>Entry</th><th>Current</th><th>P&L</th>
      <th>SL</th><th>TP</th><th>Opened</th><th>Conf</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderTrades(log) {
  const wrap = document.getElementById('trades-wrap');
  if (!log.length) {
    wrap.innerHTML = '<div class="empty">No closed trades yet.</div>';
    return;
  }
  let rows = log.map(t => {
    const sidePill    = t.side === 'long'   ? 'pill-long'  : 'pill-short';
    const outcomePill = t.outcome === 'win' ? 'pill-win'   : 'pill-loss';
    const pnlCls      = (t.pnl || 0) >= 0  ? 'green'      : 'red';
    const pnlStr      = ((t.pnl || 0) >= 0 ? '+' : '') + (t.pnl || 0).toFixed(4);
    return `<tr>
      <td><b>${t.symbol || '—'}</b></td>
      <td><span class="pill ${sidePill}">${(t.side||'').toUpperCase()}</span></td>
      <td style="color:#94a3b8;font-size:0.76rem">${t.strategy || '—'}</td>
      <td><span class="pill ${outcomePill}">${(t.outcome||'').toUpperCase()}</span></td>
      <td class="${pnlCls}" style="font-family:monospace">${pnlStr}</td>
      <td style="font-family:monospace;color:#64748b">$${(t.entry_price||0).toFixed(2)}</td>
      <td style="font-family:monospace;color:#64748b">$${(t.exit_price||0).toFixed(2)}</td>
      <td style="color:#3d5268;font-size:0.75rem">${t.opened_at||'—'}</td>
      <td style="color:#3d5268;font-size:0.75rem">${t.closed_at||'—'}</td>
    </tr>`;
  }).join('');
  wrap.innerHTML = `<table>
    <thead><tr>
      <th>Symbol</th><th>Side</th><th>Strategy</th>
      <th>Outcome</th><th>P&L</th><th>Entry</th><th>Exit</th>
      <th>Opened</th><th>Closed</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// ── Controls ──────────────────────────────────────────────────────────────────
function updateRisk(v) {
  document.getElementById('risk-val').textContent = v + '%';
}
async function applyRisk(v) {
  await fetch('/api/configure', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({risk_pct: parseFloat(v)})});
}
async function toggleDemo() {
  const checked = document.getElementById('demo-toggle').checked;
  renderModeLabel(checked);
  await fetch('/api/configure', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({demo_mode: checked})});
}
async function startBot() {
  await fetch('/api/start', {method:'POST'});
  document.getElementById('badge-run').className  = 'badge badge-run';
  document.getElementById('badge-run').innerHTML  = '<span class="dot dot-green"></span> Running';
}
async function stopBot() {
  await fetch('/api/stop', {method:'POST'});
  document.getElementById('badge-run').className  = 'badge badge-stop';
  document.getElementById('badge-run').textContent = '⏹ Stopped';
}

async function connectAPI() {
  const key  = document.getElementById('inp-key').value.trim();
  const sec  = document.getElementById('inp-secret').value.trim();
  const pass = document.getElementById('inp-pass').value.trim();
  const res  = document.getElementById('connect-result');
  if (!key) { res.className = 'red'; res.textContent = 'Enter an API key first.'; return; }
  res.className = 'amber'; res.textContent = 'Connecting…';
  try {
    const r = await fetch('/api/connect', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({api_key: key, secret: sec, passphrase: pass})});
    const d = await r.json();
    res.className = d.ok ? 'green' : 'red';
    res.textContent = d.msg;
    if (d.ok) {
      document.getElementById('demo-toggle').checked = false;
      renderModeLabel(false);
      await fetch('/api/configure', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({demo_mode: false})});
    }
  } catch(e) {
    res.className = 'red'; res.textContent = 'Connection error.';
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
startPolling();
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("BloFin Adaptive Bot Dashboard → http://localhost:8081")
    print("Demo mode ON  ·  enter API credentials in the UI to go live")
    app.run(host="0.0.0.0", port=8081, debug=False)
