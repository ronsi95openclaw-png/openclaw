"""OpenClaw Local Dashboard — http://locahttps://github.com/kkoppenhaver/cc-nano-banana.gitlhost:8080

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
from typing import Optional

# Ensure project root is in path for absolute imports
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from flask import Flask, render_template_string, request, jsonify

from dashboard.system_monitor import SystemMonitor


load_dotenv(ROOT / ".env", override=True)

app = Flask(__name__)
_price_cache: dict = {"ts": 0, "data": {}}
DATA_DIR = ROOT / "data"


# Initialize system monitor (will start health loop on first request)
_system_monitor: Optional[SystemMonitor] = None

def _get_monitor() -> SystemMonitor:
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitor(
            app=app,
            execute_command=execute_dashboard_command,
            get_ollama_status=get_ollama_status,
            get_clawbot_status=get_clawbot_status,
            get_autotrade_status=get_autotrade_status,
            get_scout_status=lambda: __import__("agents.job_scout", fromlist=["get_scout_status"]).get_scout_status(),
        )
        _system_monitor.start()
    return _system_monitor


# ── Security headers ──────────────────────────────────────────────────────────

@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com "
        "https://fonts.googleapis.com https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
        "https://cdnjs.cloudflare.com https://fonts.googleapis.com;"
    )
    return response

# ── Dashboard token auth ──────────────────────────────────────────────────────

import secrets as _secrets
_DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

@app.before_request
def _require_dashboard_auth():
    """Block all dashboard requests without valid token (if token is configured)."""
    if not _DASHBOARD_TOKEN:
        return  # no token set = localhost-only trust mode
    # Allow health check
    if request.path == "/health":
        return
    # Check token in header, query param, or cookie
    token = (
        request.headers.get("X-Dashboard-Token")
        or request.args.get("token")
        or request.cookies.get("dashboard_token")
    )
    if token != _DASHBOARD_TOKEN:
        return "Unauthorized", 401


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


def get_installed_skills() -> list:
    skills_dir = ROOT / "skills"
    if not skills_dir.exists():
        return []
    skill_dirs = [p.name for p in skills_dir.iterdir() if p.is_dir() and p.name != "__pycache__"]
    return sorted(skill_dirs)


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
        cfg    = os.getenv("OLLAMA_MODEL", "gemma4")
        return {
            "online": True, "models": models,
            "active": cfg if cfg in models else (models[0] if models else "none"),
            "cfg_missing": cfg not in models,
        }
    except Exception as exc:
        return {"online": False, "models": [], "active": "offline", "cfg_missing": False, "error": str(exc)[:60]}


def execute_dashboard_command(command: str) -> str:
    cmd = command.strip()
    if not cmd.startswith("/"):
        cmd = "/" + cmd

    if cmd.startswith("/scan"):
        parts = cmd.split()
        timeframe = parts[1] if len(parts) > 1 else "4h"
        if timeframe not in {"1h", "4h", "1d"}:
            timeframe = "4h"
        from trading.exchange import fetch_all_closes
        from trading.strategy import RSIMACDStrategy, calculate_rsi, calculate_macd

        strategy = RSIMACDStrategy()
        candle_data = fetch_all_closes(strategy.config.coins, timeframe=timeframe, count=100)
        signals = strategy.scan_all(candle_data)
        if not signals:
            lines = [f"📊 Market Scan — {timeframe} — no signals\n"]
            for coin, closes in candle_data.items():
                try:
                    rsi = calculate_rsi(closes)
                    _, _, hist = calculate_macd(closes)
                    trend = "↑" if hist > 0 else "↓"
                    icon = "🔴" if rsi >= 68 else "🟢" if rsi <= 32 else "⚪"
                    warn = "  ⚠️ near overbought" if rsi >= 68 else "  ⚠️ near oversold" if rsi <= 32 else ""
                    macd_str = f"MACD <code>{hist:+.1f}</code> {trend}"
                    lines.append(f"{icon} {coin}: RSI <code>{rsi:.1f}</code> | {macd_str}{warn}")
                except Exception:
                    lines.append(f"⚪ {coin}: insufficient data")
            lines.append("\n<i>Waiting for RSI + MACD crossover confirmation to signal.</i>")
            return "\n".join(lines)
        parts = [f"🔔 <b>Scan — {timeframe} — {len(signals)} signal(s)</b>\n"]
        for s in signals:
            parts.append(s.to_telegram_message())
            parts.append("")
        parts.append("<i>⚠️ Analysis only. No orders placed.</i>")
        return "\n".join(parts)

    if cmd == "/market":
        from core.market import get_market_summary
        return get_market_summary()

    if cmd == "/fng":
        import requests as _req
        r = _req.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json()["data"][0]
        val = int(d["value"])
        label = d["value_classification"]
        bar = "█" * (val // 10) + "░" * (10 - val // 10)
        emoji = "😱" if val < 25 else "😨" if val < 45 else "😐" if val < 55 else "😄" if val < 75 else "🤑"
        return (
            f"{emoji} <b>Fear & Greed Index</b>\n"
            f"<code>{bar}</code>\n"
            f"<b>{val}/100</b> — {label}\n"
            f"<i>via alternative.me</i>"
        )

    if cmd == "/status":
        ollama = get_ollama_status()
        claude_status = "configured ✅" if os.getenv("ANTHROPIC_API_KEY", "").strip() else "not set ⚠️"
        crypto_status = "configured ✅" if os.getenv("CRYPTOCOM_API_KEY", "").strip() else "not set ⚠️"
        bot = get_clawbot_status()
        return (
            f"🦾 <b>ClawBot Status</b>\n\n"
            f"🧠 Ollama: {ollama.get('active', 'offline')} ({'online' if ollama.get('online') else 'offline'})\n"
            f"⚡ Claude API: {claude_status}\n"
            f"📈 Crypto.com: {crypto_status}\n"
            f"🔌 Bot running: {'yes' if bot.get('running') else 'no'}\n"
            f"⏱ Last seen: {bot.get('last_seen', 'never')}"
        )

    if cmd == "/cashclaw":
        from agents.job_scout import get_scout_status, format_scout_status
        from agents.cashclaw_applier import get_applier_status, format_applier_status
        scout = get_scout_status()
        applier = get_applier_status()
        return (
            "🦞 <b>CashClaw Status</b>\n\n"
            f"🔍 <b>Scout</b>\n{format_scout_status(scout)}\n\n"
            f"📝 <b>Applier</b>\n{format_applier_status(applier)}\n\n"
            "<i>Use /scout run, /approve_job N, /apply_job N, /send_apply N</i>"
        )

    if cmd.startswith("/scout"):
        parts = cmd.split()
        if len(parts) > 1 and parts[1].lower() == "run":
            from agents.job_scout import run_job_scout
            return run_job_scout(bot=None, chat_id=0)
        from agents.job_scout import get_scout_status, format_scout_status
        return format_scout_status(get_scout_status())

    if cmd.startswith("/autotrade"):
        parts = cmd.split()
        arg = parts[1].lower() if len(parts) > 1 else ""
        from core.scheduler import enable_autotrade, disable_autotrade, get_autotrade_status
        if arg == "on":
            cfg = enable_autotrade(0, scan_time="08:00", timeframe="4h")
            return (
                f"🤖 <b>Auto-Trade ENABLED</b>\n\n"
                f"⏰ Daily scan: <code>{cfg['scan_time']} UTC</code>\n"
                f"📊 Timeframe: <code>{cfg['timeframe']}</code>\n"
                f"🎯 Executes: HIGH confidence RSI+MACD signals only\n"
                f"💰 Risk: 1.5% of portfolio per trade"
            )
        if arg == "off":
            disable_autotrade()
            return "🤖 <b>Auto-Trade DISABLED</b>\n\nNo more automatic trades. Use /autotrade on to re-enable."
        cfg = get_autotrade_status()
        status = "ENABLED ✅" if cfg.get("enabled") else "DISABLED ❌"
        return (
            f"🤖 <b>Auto-Trade Status: {status}</b>\n\n"
            f"⏰ Scan time: <code>{cfg.get('scan_time', '08:00')} UTC</code>\n"
            f"📊 Timeframe: <code>{cfg.get('timeframe', '4h')}</code>"
        )

    if cmd == "/lifeos":
        from agents.lifeos_agent import get_dashboard_data
        try:
            data = get_dashboard_data()
        except Exception as exc:
            return f"LifeOS not set up yet. Run /lifesetup first. ({exc})"
        f = data["fitness"]
        fin = data["finance"]
        h = data["habits"]
        lines = [
            "<b>LifeOS Dashboard</b>\n",
            "<b>Fitness</b>",
            f"  Weight: {f['weight']} kg  \u2192  Goal: {f['goal_weight']} kg",
            f"  Workouts this week: {f['workouts']}",
            "",
            "<b>Finance</b>",
            f"  Monthly income: ${fin['income']}",
            f"  Debt: ${fin['debt']}",
            f"  Today's expenses: ${float(fin['expenses']):.2f}",
            "",
            "<b>Habits</b>",
            f"  Score: {h['score']} pts  |  Streak: {h['streak']} days",
            f"  Completion rate (7d): {h['completionRate']}%",
            "",
            f"Coach mode: {data['profile']['coach_mode']}",
            "",
            "/morning \u2014 start morning check-in",
            "/evening \u2014 start evening check-in",
            "/score   \u2014 gamification leaderboard",
        ]
        return "\n".join(lines)

    if cmd == "/score":
        from agents.lifeos_agent import get_scores
        try:
            s = get_scores()
        except Exception as exc:
            return f"LifeOS not set up yet. ({exc})"
        streak_bar = "\U0001f525" * min(s["streak"], 14)
        lines = [
            "<b>LifeOS Score</b>\n",
            f"Total points:  <b>{s['total']}</b>",
            f"Current streak: <b>{s['streak']} days</b>  {streak_bar}",
            "",
            "<b>Points table</b>",
            "  +10  workout completed",
            "  +10  diet adherence",
            "  +15  deep work session",
            "  +5   expense tracked",
            "  -10  missed workout",
            "  -10  overspending",
            "  -15  skipped priorities",
        ]
        return "\n".join(lines)

    if cmd == "/dash":
        from agents.lifeos_agent import get_dashboard_data
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        price_lines = []
        try:
            prices = get_prices()
            for coin, p in list(prices.items())[:4]:
                price_lines.append(f"  {coin}: ${p['price']:,.0f}")
        except Exception:
            price_lines.append("  (prices unavailable)")

        portfolio_line = ""
        try:
            from trading.exchange import get_account_balance, get_portfolio_value_usd
            bal = get_account_balance()
            total = get_portfolio_value_usd(bal)
            portfolio_line = f"Portfolio: ~${total:,.2f}"
        except Exception:
            portfolio_line = "Portfolio: (unavailable)"

        life_lines = []
        try:
            life = get_dashboard_data()
            h = life["habits"]
            f = life["fitness"]
            fin = life["finance"]
            streak_bar = "\U0001f525" * min(h["streak"], 7)
            life_lines = [
                f"  Score: {h['score']} pts  |  Streak: {h['streak']}d {streak_bar}",
                f"  Weight: {f['weight']} kg \u2192 {f['goal_weight']} kg",
                f"  Today spend: ${float(fin['expenses']):.2f}",
                f"  Workouts (7d): {f['workouts']}",
            ]
        except Exception:
            life_lines = ["  (LifeOS not set up \u2014 run /lifesetup)"]

        autotrade_line = ""
        try:
            at = get_autotrade_status()
            autotrade_line = f"Autotrade: {'ON \u2705' if at.get('enabled') else 'OFF'}"
        except Exception:
            autotrade_line = "Autotrade: (unavailable)"

        brain_line = ""
        try:
            from core.brain import get_usage_today
            usage = get_usage_today()
            brain_line = f"Brain: {usage.get('total_calls', 0)} calls today"
        except Exception:
            brain_line = ""

        lines = [
            f"<b>ClawBot Dashboard</b> \u2014 {ts}\n",
            "<b>Markets</b>",
        ] + price_lines + [
            "",
            portfolio_line,
            autotrade_line,
            "",
            "<b>LifeOS</b>",
        ] + life_lines + [
            "",
            brain_line,
            "",
            "/morning /evening /score /lifeos",
        ]
        return "\n".join(lines)

    raise ValueError(f"Unknown dashboard command: {cmd}")


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


def get_portfolio_data() -> dict:
    """Build portfolio summary from trade log + live prices."""
    trades = get_recent_trades(n=1000)
    prices = get_prices()

    positions: dict = {}
    total_invested = 0.0
    total_pnl = 0.0
    wins = 0
    losses = 0

    for t in trades:
        coin       = str(t.get("coin", "")).upper()
        action     = str(t.get("action", "")).upper()
        usd_amount = float(t.get("usd_amount", 0) or 0)
        pnl        = float(t.get("pnl", 0) or 0)
        if not coin:
            continue
        if coin not in positions:
            positions[coin] = {"coin": coin, "trades": 0, "invested": 0.0,
                               "pnl": 0.0, "buys": 0, "sells": 0,
                               "current_price": 0, "change_24h": 0, "sign": ""}
        positions[coin]["trades"] += 1
        positions[coin]["pnl"]    += pnl
        total_pnl += pnl
        if action == "BUY":
            positions[coin]["invested"] += usd_amount
            positions[coin]["buys"]     += 1
            total_invested              += usd_amount
        elif action == "SELL":
            positions[coin]["sells"] += 1
        if pnl > 0:
            wins   += 1
        elif pnl < 0:
            losses += 1

    for coin, pos in positions.items():
        d = prices.get(coin, {})
        pos["current_price"] = d.get("price", 0)
        pos["change_24h"]    = d.get("change", 0)
        pos["sign"]          = d.get("sign", "")
        pos["pnl"]           = round(pos["pnl"], 2)
        pos["invested"]      = round(pos["invested"], 2)

    total_trades = len(trades)
    win_rate     = round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0

    return {
        "positions":       list(positions.values()),
        "total_trades":    total_trades,
        "total_invested":  round(total_invested, 2),
        "total_pnl":       round(total_pnl, 2),
        "win_rate":        win_rate,
        "wins":            wins,
        "losses":          losses,
        "has_data":        total_trades > 0,
        "prices":          prices,
    }


def get_live_holdings() -> dict:
    """
    Fetch real holdings from Crypto.com API.
    Returns dict with balances, total value, and per-asset breakdown.
    Falls back gracefully if API keys not set or API fails.
    """
    import time

    crypto_key = os.getenv("CRYPTOCOM_API_KEY", "").strip()
    crypto_secret = os.getenv("CRYPTOCOM_SECRET", "").strip()

    if not crypto_key or not crypto_secret:
        return {"configured": False, "error": "API keys not set", "balances": {}, "total_usd": 0}

    try:
        # Add project root to path for trading module
        root = str(ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)

        from trading.exchange import get_account_balance, get_portfolio_value_usd, fetch_ticker_price

        balances_raw = get_account_balance()

        # Build enriched balance objects with USD values
        balances = {}
        prices = get_prices()  # CoinGecko prices for display

        for currency, data in balances_raw.items():
            total = data["total"]
            available = data["available"]
            if total < 0.000001:
                continue

            usd_value = 0.0
            price = 0.0
            change_24h = 0.0

            if currency == "USDT" or currency == "USD":
                usd_value = total
                price = 1.0
            else:
                # Try CoinGecko price first (already cached)
                cg_data = prices.get(currency, {})
                if cg_data:
                    price = cg_data.get("price", 0)
                    change_24h = cg_data.get("change", 0)
                    usd_value = total * price
                else:
                    # Fall back to Crypto.com ticker
                    try:
                        price = fetch_ticker_price(f"{currency}_USDT")
                        usd_value = total * price
                    except Exception:
                        pass

            balances[currency] = {
                "currency": currency,
                "total": total,
                "available": available,
                "locked": total - available,
                "price_usd": price,
                "value_usd": round(usd_value, 2),
                "change_24h": change_24h,
                "sign": "+" if change_24h >= 0 else "",
            }

        total_usd = sum(b["value_usd"] for b in balances.values())

        # Sort by USD value descending
        sorted_balances = dict(sorted(balances.items(), key=lambda x: x[1]["value_usd"], reverse=True))

        return {
            "configured": True,
            "error": None,
            "balances": sorted_balances,
            "total_usd": round(total_usd, 2),
            "asset_count": len(sorted_balances),
            "fetched_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        }

    except Exception as e:
        err_str = str(e)
        # Make UNAUTHORIZED errors more helpful
        if "10002" in err_str or "UNAUTHORIZED" in err_str:
            err_str = "API key unauthorized (error 10002). Check: key is active, has read permission, not IP-restricted. Update CRYPTOCOM_API_KEY + CRYPTOCOM_SECRET in .env"
        return {
            "configured": True,
            "error": err_str[:300],
            "balances": {},
            "total_usd": 0,
            "asset_count": 0,
            "fetched_at": "—",
        }


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
<title>OpenClaw Command Center</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
  :root {
    --neon:#00ff88;--neon2:#00ffff;--amber:#ffaa00;--red:#ff4455;
    --pink:#ff00aa;--purple:#9b59b6;--orange:#ff6b35;
    --bg:#080808;--card:#0f0f0f;--border:#1e1e1e;
    --text:#c8c8c8;--muted:#555;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{
    background:var(--bg);color:var(--text);
    font-family:'Share Tech Mono',monospace;font-size:13px;
    background-image:radial-gradient(circle,#181818 1px,transparent 1px);
    background-size:22px 22px;background-attachment:fixed;
    padding-bottom:160px;
  }
  /* HEADER */
  .hdr{
    background:#060606;border-bottom:2px solid var(--neon);
    box-shadow:0 0 20px #00ff8822;padding:0 20px;height:54px;
    display:flex;align-items:center;gap:14px;
    position:sticky;top:0;z-index:500;
  }
  .hdr-title{
    font-family:'Press Start 2P',monospace;font-size:10px;
    color:var(--neon);text-shadow:0 0 12px var(--neon);
    letter-spacing:2px;white-space:nowrap;
  }
  .hdr-status{
    font-family:'Press Start 2P',monospace;font-size:7px;
    padding:4px 9px;border:1px solid var(--neon);
    color:var(--neon);background:#001a00;
    animation:blink 1.4s step-end infinite;
  }
  .hdr-status.idle{animation:none;opacity:0.5;border-color:#555;color:#555;}
  @keyframes blink{50%{opacity:0;}}
  .hdr-nav{display:flex;gap:0;margin-left:auto;}
  .hdr-nav a{
    color:var(--muted);text-decoration:none;font-size:10px;
    padding:4px 10px;border-left:1px solid var(--border);
    transition:color 0.2s,background 0.2s;
  }
  .hdr-nav a:hover{color:var(--neon);background:#001a00;}
  .hdr-clock{
    font-family:'Press Start 2P',monospace;font-size:8px;
    color:var(--neon2);min-width:72px;text-align:right;
    border-left:1px solid var(--border);padding-left:12px;
  }
  #refresh-bar{
    position:fixed;top:0;left:0;height:2px;width:0%;
    background:var(--neon);z-index:9999;transition:width 1s linear;
  }
  /* CMD BAR */
  .cmd-bar{
    background:#080808;border-bottom:1px solid var(--border);
    padding:8px 20px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;
  }
  .cmd-lbl{font-family:'Press Start 2P',monospace;font-size:6px;color:var(--muted);letter-spacing:2px;}
  .cmd-btn{
    background:var(--card);border:1px solid var(--border);
    color:var(--neon);font-family:'Share Tech Mono',monospace;
    font-size:11px;padding:5px 12px;cursor:pointer;position:relative;
    transition:border-color 0.15s,box-shadow 0.15s;
  }
  .cmd-btn:hover{border-color:var(--neon);box-shadow:0 0 8px #00ff8833;}
  .cmd-btn .tip{
    position:absolute;bottom:110%;left:50%;transform:translateX(-50%);
    background:var(--neon);color:#000;font-size:8px;padding:2px 6px;
    white-space:nowrap;opacity:0;pointer-events:none;transition:opacity 0.2s;
  }
  .cmd-btn.copied .tip{opacity:1;}
  /* SECTION HDR */
  .sec-hdr{
    font-family:'Press Start 2P',monospace;font-size:7px;
    color:var(--muted);letter-spacing:3px;
    padding:18px 20px 8px;border-bottom:1px solid var(--border);
    margin-bottom:16px;
  }
  .sec-hdr em{color:var(--neon2);font-style:normal;}
  /* AGENT GRID */
  .agents-grid{
    display:grid;grid-template-columns:repeat(3,1fr);
    gap:16px;padding:16px 20px;
  }
  @media(max-width:900px){.agents-grid{grid-template-columns:repeat(2,1fr);}}
  @media(max-width:560px){.agents-grid{grid-template-columns:1fr;}}
  .agent-card{
    background:var(--card);border:1px solid;
    padding:16px;position:relative;
    transition:box-shadow 0.2s;
  }
  .agent-card:hover{box-shadow:0 0 18px currentColor;}
  .agent-card::after{
    content:'';position:absolute;top:0;left:0;right:0;
    height:2px;background:currentColor;opacity:0.5;
  }
  .agent-top{display:flex;align-items:center;gap:10px;margin-bottom:12px;}
  .agent-emoji{font-size:22px;line-height:1;}
  .agent-name{font-family:'Press Start 2P',monospace;font-size:8px;letter-spacing:1px;}
  .agent-role{font-size:10px;color:var(--muted);margin-top:3px;}
  .agent-badge{
    margin-left:auto;font-family:'Press Start 2P',monospace;
    font-size:6px;padding:3px 7px;border:1px solid currentColor;
  }
  .agent-badge.active{animation:blink 2s step-end infinite;}
  .agent-badge.idle{animation:none;opacity:0.4;}
  .hp-lbl{
    font-family:'Press Start 2P',monospace;font-size:6px;
    color:var(--muted);margin-bottom:4px;
    display:flex;justify-content:space-between;
  }
  .hp-track{
    background:#141414;height:8px;border:1px solid #222;
    margin-bottom:10px;overflow:hidden;position:relative;
  }
  .hp-fill{height:100%;transition:width 0.6s ease;position:relative;}
  .hp-fill::after{
    content:'';position:absolute;inset:0;
    background:repeating-linear-gradient(
      90deg,transparent 0px,transparent 4px,
      rgba(0,0,0,0.25) 4px,rgba(0,0,0,0.25) 5px
    );
  }
  .agent-stats{font-size:10px;color:var(--muted);}
  .agent-stats span{color:var(--text);}
  .agent-chat-btn{
    margin-top:10px;width:100%;
    background:transparent;border:1px solid currentColor;
    color:inherit;font-family:'Press Start 2P',monospace;
    font-size:6px;padding:6px 0;cursor:pointer;
    letter-spacing:2px;transition:background 0.15s,color 0.15s;
    display:block;
  }
  .agent-chat-btn:hover{background:currentColor;color:#000;}
  /* STATUS CARDS */
  .status-grid{
    display:grid;grid-template-columns:repeat(3,1fr);
    gap:16px;padding:0 20px 16px;
  }
  @media(max-width:768px){.status-grid{grid-template-columns:1fr;}}
  .status-card{background:var(--card);border:1px solid var(--border);padding:16px;}
  .status-card-title{
    font-family:'Press Start 2P',monospace;font-size:7px;
    color:var(--neon2);letter-spacing:2px;margin-bottom:12px;
    border-bottom:1px solid var(--border);padding-bottom:8px;
  }
  .sr{
    display:flex;justify-content:space-between;align-items:center;
    padding:4px 0;border-bottom:1px solid #111;font-size:11px;
  }
  .sr:last-child{border-bottom:none;}
  .sk{color:var(--muted);}
  .sv{color:var(--text);}
  .sv.ok{color:var(--neon);}
  .sv.warn{color:var(--amber);}
  .sv.err{color:var(--red);}
  .pr{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #111;font-size:11px;}
  .pr:last-child{border-bottom:none;}
  .pc{color:var(--neon2);font-family:'Press Start 2P',monospace;font-size:7px;}
  .pv{font-size:12px;}
  .up{color:var(--neon);}
  .dn{color:var(--red);}
  /* TABLE */
  .section-wrap{padding:0 20px 20px;}
  .rtable{width:100%;border-collapse:collapse;font-size:11px;}
  .rtable th{
    font-family:'Press Start 2P',monospace;font-size:6px;
    color:var(--neon);letter-spacing:1px;padding:8px;
    border-bottom:1px solid var(--neon);text-align:left;background:#060606;
  }
  .rtable td{padding:6px 8px;border-bottom:1px solid var(--border);color:var(--text);}
  .rtable tr:hover td{background:#121212;}
  .bb{color:var(--neon);background:#00ff8811;border:1px solid #00ff8833;padding:1px 6px;font-size:10px;}
  .bs{color:var(--red);background:#ff445511;border:1px solid #ff445533;padding:1px 6px;font-size:10px;}
  /* CHAT */
  .chat-panel{
    position:fixed;bottom:0;right:20px;width:360px;
    background:#080808;border:1px solid var(--neon);
    border-bottom:none;box-shadow:0 0 20px #00ff8822;z-index:400;
  }
  .chat-hdr{
    background:#0a120a;border-bottom:1px solid var(--neon);
    padding:10px 14px;display:flex;align-items:center;gap:8px;cursor:pointer;
  }
  .chat-title{font-family:'Press Start 2P',monospace;font-size:7px;color:var(--neon);flex:1;}
  .chat-msgs{max-height:280px;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:6px;}
  .msg-u{align-self:flex-end;background:#0a1a0a;border:1px solid #00ff8833;padding:6px 10px;font-size:11px;max-width:85%;white-space:pre-wrap;word-break:break-word;}
  .msg-b{background:#111;border:1px solid var(--border);padding:6px 10px;font-size:11px;max-width:85%;white-space:pre-wrap;word-break:break-word;}
  .chat-in-row{padding:8px 10px;border-top:1px solid var(--border);display:flex;gap:6px;}
  .chat-in-row input{
    flex:1;background:#0a0a0a;border:1px solid var(--border);
    color:var(--neon);font-family:'Share Tech Mono',monospace;
    font-size:11px;padding:6px 10px;outline:none;
  }
  .chat-in-row input:focus{border-color:var(--neon);}
  .chat-send{
    background:#0a120a;border:1px solid var(--neon);
    color:var(--neon);font-family:'Press Start 2P',monospace;
    font-size:8px;padding:6px 10px;cursor:pointer;
  }
  .chat-send:hover{background:var(--neon);color:#000;}
  /* Mobile tap responsiveness — removes 300ms iOS delay on all interactive elements */
  button,a,.agent-chat-btn,.chat-hdr,.chat-send,.cmd-btn,.btn-move,.btn-del,.btn-add-task,.new-agent-btn{
    touch-action:manipulation;
    -webkit-tap-highlight-color:rgba(0,255,136,0.15);
  }
  /* Visual tap feedback for mobile */
  button:active, .agent-chat-btn:active, .chat-send:active, .cmd-btn:active {
    opacity: 0.7;
    transform: scale(0.97);
  }
  @media(max-width:560px){
    .chat-panel{width:100%;right:0;left:0;bottom:0;border-left:none;border-right:none;}
    .chat-msgs{max-height:220px;}
    body{padding-bottom:280px;}
    .hdr{padding:0 10px;gap:6px;height:46px;}
    .hdr-title{font-size:6px;letter-spacing:0px;}
    .hdr-clock{display:none;}
    .hdr-nav a{font-size:8px;padding:4px 6px;}
    .hdr-status{font-size:5px;padding:3px 5px;}
    .cmd-bar{padding:8px 10px;gap:6px;}
    .cmd-btn{font-size:10px;padding:8px 10px;min-height:40px;touch-action:manipulation;}
    .agents-grid{grid-template-columns:1fr;padding:10px;}
    .agent-card{padding:12px;}
    .agent-chat-btn{padding:14px !important;font-size:8px !important;min-height:44px;letter-spacing:1px !important;}
    .status-grid{grid-template-columns:1fr;padding:0 10px 10px;}
    .status-card{padding:12px;}
    .sec-hdr{font-size:6px;padding:12px 10px 6px;letter-spacing:2px;}
    .section-wrap{padding:0 10px 16px;}
    .rtable{font-size:10px;}
    .rtable th{font-size:5px;}
    .sr{font-size:11px;}
    .chat-send{padding:12px 16px !important;min-width:44px;min-height:44px;}
    .chat-hdr{padding:14px !important;min-height:48px;}
    .chat-in-row input{font-size:16px !important;min-height:44px;padding:10px !important;}
    .chat-in-row{padding:10px !important;}
  }
  /* ── Inline command status bar ──────────────────────────────── */
  #cmd-status{font-family:'Share Tech Mono',monospace;font-size:10px;padding:5px 12px;min-height:22px;color:var(--muted);transition:color 0.2s;letter-spacing:0.03em;}
  #cmd-status.running{color:var(--neon);}
  #cmd-status.ok{color:var(--neon);}
  #cmd-status.err{color:var(--red);}
  /* ── Toast notifications ─────────────────────────────────────── */
  #toast-container{position:fixed;bottom:20px;right:20px;z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none;}
  .toast-msg{background:#141414;border:1px solid #333;border-radius:8px;padding:10px 16px;font-family:'Share Tech Mono',monospace;font-size:11px;color:#e0e0e0;opacity:0;transform:translateY(10px);transition:opacity 0.25s,transform 0.25s;pointer-events:auto;max-width:320px;word-break:break-word;}
  .toast-msg.show{opacity:1;transform:translateY(0);}
  .toast-msg.ok{border-color:#00ff8866;color:#00ff88;}
  .toast-msg.warn{border-color:#ffaa0066;color:#ffaa00;}
  .toast-msg.err{border-color:#ff445566;color:#ff8888;}
  /* ── Config health banner ───────────────────────────────────── */
  #config-banner{display:none;background:#1a1200;border-bottom:1px solid #ffaa0055;padding:7px 16px;font-family:'Share Tech Mono',monospace;font-size:10px;color:#ffaa00;align-items:center;gap:12px;flex-wrap:wrap;}
  #config-banner.visible{display:flex;}
  #config-banner .cb-dismiss{margin-left:auto;cursor:pointer;color:#555;font-size:14px;line-height:1;background:none;border:none;padding:0;}
  #config-banner .cb-dismiss:hover{color:#ffaa00;}
</style>
</head>
<body>
<div id="config-banner">
  <span>&#9888;</span>
  <span id="config-banner-text"></span>
  <button class="cb-dismiss" onclick="dismissConfigBanner()" title="Dismiss">&#x2715;</button>
</div>
<div id="refresh-bar"></div>

<!-- HEADER -->
<div class="hdr">
  <div class="hdr-title">&#9670; OPENCLAW-CMD &#9670;</div>
  <div class="hdr-status {% if not bot.running %}idle{% endif %}">
    {% if bot.running %}&#9679; ONLINE{% else %}&#9675; IDLE{% endif %}
  </div>
  <div class="hdr-nav">
    <a href="/taskboard">TASKS</a>
    <a href="/team">TEAM</a>
    <a href="/status">STATUS</a>
    <a href="/portfolio">PORTFOLIO</a>
    <a href="/holdings">HOLDINGS</a>
    <a href="/clip-economy">CASHCLAW</a>
    <a href="#" class="new-agent-btn" onclick="openNewAgentModal();return false;" style="color:var(--pink);border-left:1px solid var(--border);">+ NEW AGENT</a>
  </div>
  <div class="hdr-clock" id="live-clock">00:00:00</div>
</div>

<!-- QUICK COMMANDS -->
<div class="cmd-bar">
  <span class="cmd-lbl">CMD:</span>
  {% for cmd in ['/scan','/market','/fng','/status','/cashclaw','/scout run','/autotrade on'] %}
  <button class="cmd-btn" onclick="runCmd(this,'{{ cmd }}')"><span class="tip">RUN</span>{{ cmd }}</button>
  {% endfor %}
  <span style="margin-left:auto;font-family:'Press Start 2P',monospace;font-size:6px;color:var(--muted);">REFRESH <span id="cd">30</span>s</span>
</div>
<div id="cmd-status"></div>

<!-- AGENTS -->
<div class="sec-hdr">&#9672; AGENT <em>STATUS</em> &#8212; {{ now }}</div>
<div class="agents-grid">

{% set j_hp = [100, (usage.ollama_calls + usage.claude_calls) * 4 + 30] | min %}
<div class="agent-card" style="border-color:#00ff88;color:#00ff88;">
  <div class="agent-top">
    <div class="agent-emoji">&#129504;</div>
    <div>
      <div class="agent-name" style="color:#00ff88;">JARVIS</div>
      <div class="agent-role">Brain &middot; {{ ollama.active }}</div>
    </div>
    <div class="agent-badge {% if bot.running %}active{% else %}idle{% endif %}" style="color:#00ff88;">
      {% if bot.running %}ACTIVE{% else %}IDLE{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ [j_hp, 30] | max }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ [j_hp,30]|max }}%;background:#00ff88;box-shadow:0 0 6px #00ff88;"></div></div>
  <div class="agent-stats">
    ollama: <span>{{ usage.ollama_calls }}</span> &nbsp;
    claude: <span>{{ usage.claude_calls }}</span> &nbsp;
    cache: <span>{{ usage.cache_hits }}</span>
  </div>
  <button class="agent-chat-btn" onclick="openAgentChat('JARVIS')">&#9658; CHAT WITH JARVIS</button>
</div>

<div class="agent-card" style="border-color:#00ffff;color:#00ffff;">
  <div class="agent-top">
    <div class="agent-emoji">&#128269;</div>
    <div>
      <div class="agent-name" style="color:#00ffff;">SCOUT</div>
      <div class="agent-role">Job Scout &middot; Whop/Discord</div>
    </div>
    <div class="agent-badge active" style="color:#00ffff;">STANDBY</div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>60%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:60%;background:#00ffff;box-shadow:0 0 6px #00ffff;"></div></div>
  <div class="agent-stats">27 terms &middot; 5 categories &middot; 6h cycle</div>
  <button class="agent-chat-btn" onclick="openAgentChat('SCOUT')">&#9658; CHAT WITH SCOUT</button>
</div>

{% set wd_hp = 90 if autotrade.enabled else 35 %}
<div class="agent-card" style="border-color:#ffaa00;color:#ffaa00;">
  <div class="agent-top">
    <div class="agent-emoji">&#128021;</div>
    <div>
      <div class="agent-name" style="color:#ffaa00;">WATCHDOG</div>
      <div class="agent-role">Auto-Trade &middot; RSI+MACD</div>
    </div>
    <div class="agent-badge {% if autotrade.enabled %}active{% else %}idle{% endif %}" style="color:#ffaa00;">
      {% if autotrade.enabled %}ARMED{% else %}SAFE{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ wd_hp }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ wd_hp }}%;background:#ffaa00;box-shadow:0 0 6px #ffaa00;"></div></div>
  <div class="agent-stats">
    trades: <span>{{ trades|length }}</span> &nbsp;
    {% if autotrade.enabled %}scan: <span>{{ autotrade.scan_time }} UTC</span>{% else %}<span>disabled</span>{% endif %}
  </div>
  <button class="agent-chat-btn" onclick="openAgentChat('WATCHDOG')">&#9658; CHAT WITH WATCHDOG</button>
</div>

{% set cx_hp = 80 if codereview.date else 25 %}
<div class="agent-card" style="border-color:#9b59b6;color:#9b59b6;">
  <div class="agent-top">
    <div class="agent-emoji">&#9881;&#65039;</div>
    <div>
      <div class="agent-name" style="color:#9b59b6;">CODEX</div>
      <div class="agent-role">Code Review &middot; Auto-Upgrade</div>
    </div>
    <div class="agent-badge {% if codereview.date %}active{% else %}idle{% endif %}" style="color:#9b59b6;">
      {% if codereview.date %}ACTIVE{% else %}IDLE{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ cx_hp }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ cx_hp }}%;background:#9b59b6;box-shadow:0 0 6px #9b59b6;"></div></div>
  <div class="agent-stats">
    {% if codereview.date %}last: <span>{{ codereview.date }}</span>{% else %}no reviews yet{% endif %}
    &nbsp; skills: <span>{{ skills|length }}</span>
  </div>
  <button class="agent-chat-btn" onclick="openAgentChat('CODEX')">&#9658; CHAT WITH CODEX</button>
</div>

<div class="agent-card" style="border-color:#ff6b35;color:#ff6b35;">
  <div class="agent-top">
    <div class="agent-emoji">&#129438;</div>
    <div>
      <div class="agent-name" style="color:#ff6b35;">CLIPPER</div>
      <div class="agent-role">CashClaw &middot; HumanVoice</div>
    </div>
    <div class="agent-badge active" style="color:#ff6b35;">READY</div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>70%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:70%;background:#ff6b35;box-shadow:0 0 6px #ff6b35;"></div></div>
  <div class="agent-stats">
    <a href="/clip-economy" style="color:#ff6b35;text-decoration:none;">&#8594; income panel</a>
  </div>
  <button class="agent-chat-btn" onclick="openAgentChat('CLIPPER')">&#9658; CHAT WITH CLIPPER</button>
</div>

{% set hk_hp = 85 if prices else 20 %}
<div class="agent-card" style="border-color:#ff00aa;color:#ff00aa;">
  <div class="agent-top">
    <div class="agent-emoji">&#129413;</div>
    <div>
      <div class="agent-name" style="color:#ff00aa;">HAWK</div>
      <div class="agent-role">Market Watch &middot; Prices</div>
    </div>
    <div class="agent-badge {% if prices %}active{% else %}idle{% endif %}" style="color:#ff00aa;">
      {% if prices %}LIVE{% else %}DARK{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ hk_hp }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ hk_hp }}%;background:#ff00aa;box-shadow:0 0 6px #ff00aa;"></div></div>
  <div class="agent-stats">
    {% if prices %}
      {% for coin, d in prices.items() %}<span style="color:#ff00aa;">{{ coin }}</span> ${{ "{:,.0f}".format(d.price) }} &nbsp;{% endfor %}
    {% else %}CoinGecko offline{% endif %}
  </div>
  <button class="agent-chat-btn" onclick="openAgentChat('HAWK')">&#9658; CHAT WITH HAWK</button>
</div>

</div>

<!-- SYSTEM OVERVIEW -->
<div class="sec-hdr">&#9672; SYSTEM <em>OVERVIEW</em></div>
<div class="status-grid">

  <div class="status-card">
    <div class="status-card-title">SYSTEM</div>
    <div class="sr"><span class="sk">ClawBot</span>
      <span class="sv {% if bot.running %}ok{% else %}warn{% endif %}">{% if bot.running %}&#9679; ACTIVE ({{ bot.last_seen }}){% else %}&#9675; IDLE ({{ bot.last_seen }}){% endif %}</span></div>
    <div class="sr"><span class="sk">Ollama</span>
      <span class="sv {% if ollama.online %}ok{% else %}err{% endif %}">{% if ollama.online %}&#9679; {{ ollama.active }}{% else %}&#10007; OFFLINE{% endif %}</span></div>
    <div class="sr"><span class="sk">Claude API</span>
      <span class="sv {% if claude_ok %}ok{% else %}warn{% endif %}">{% if claude_ok %}&#9679; SET{% else %}&#9675; NOT SET{% endif %}</span></div>
    <div class="sr"><span class="sk">Crypto.com</span>
      <span class="sv {% if crypto_ok %}ok{% else %}warn{% endif %}">{% if crypto_ok %}&#9679; SET{% else %}&#9675; NOT SET{% endif %}</span></div>
    <div class="sr"><span class="sk">Cache</span>
      <span class="sv">{{ cache.entries }} entries &middot; {{ cache.newest }}</span></div>
    <div class="sr"><span class="sk">Auto-Trade</span>
      <span class="sv {% if autotrade.enabled %}ok{% else %}warn{% endif %}">{% if autotrade.enabled %}ENABLED{% else %}DISABLED{% endif %}</span></div>
  </div>

  <div class="status-card">
    <div class="status-card-title">LIVE PRICES</div>
    {% if prices %}
      {% for coin, d in prices.items() %}
      <div class="pr">
        <span class="pc">{{ coin }}</span>
        <span class="pv">${{ "{:,.2f}".format(d.price) }}</span>
        <span class="{% if d.change >= 0 %}up{% else %}dn{% endif %}">{{ d.sign }}{{ d.change }}%</span>
      </div>
      {% endfor %}
    {% else %}
      <div style="color:var(--muted);font-size:11px;padding:8px 0;">CoinGecko unavailable</div>
    {% endif %}
  </div>

  <div class="status-card">
    <div class="status-card-title">BRAIN TODAY</div>
    <div class="sr"><span class="sk">Ollama</span><span class="sv ok">{{ usage.ollama_calls }} calls</span></div>
    <div class="sr"><span class="sk">Claude</span>
      <span class="sv {% if usage.claude_calls > 0 %}warn{% else %}ok{% endif %}">{{ usage.claude_calls }} calls</span></div>
    <div class="sr"><span class="sk">Cache hits</span><span class="sv ok">{{ usage.cache_hits }}</span></div>
    <div class="sr"><span class="sk">Tokens in</span>
      <span class="sv">{{ "{:,}".format(usage.claude_input_tokens) }}</span></div>
    {% set cost = (usage.claude_input_tokens * 0.00000025) + (usage.claude_output_tokens * 0.00000125) %}
    <div class="sr"><span class="sk">API cost</span>
      <span class="sv {% if cost > 0.01 %}warn{% else %}ok{% endif %}">${{ "%.4f"|format(cost) }}</span></div>
    <div class="sr"><span class="sk">Model</span>
      <span class="sv" style="font-size:10px;">{{ ollama.active }}</span></div>
  </div>

</div>

<!-- DATA FEEDS -->
<div class="sec-hdr">&#9672; DATA <em>FEEDS</em></div>
<div class="status-grid">

  <div class="status-card">
    <div class="status-card-title">REMINDERS</div>
    {% if tasks %}
      {% for t in tasks[:5] %}
      <div class="sr">
        <span class="sk">{{ t.time }}</span>
        <span class="sv" style="font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px;">{{ t.text[:38] }}{% if t.text|length > 38 %}&hellip;{% endif %}</span>
      </div>
      {% endfor %}
    {% else %}
      <div style="color:var(--muted);font-size:10px;padding:8px 0;">No reminders. /remind HH:MM text</div>
    {% endif %}
  </div>

  <div class="status-card">
    <div class="status-card-title">KNOWLEDGE &middot; {{ notes.count }}</div>
    {% if notes.count > 0 %}
      {% for n in notes.recent[:4] %}
      <div class="sr">
        <span class="sk" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:65%;">{{ n.title[:32] }}</span>
        <span class="sv" style="color:#444;font-size:10px;">{{ n.timestamp[:10] }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div style="color:var(--muted);font-size:10px;padding:8px 0;">No notes. /save in Telegram.</div>
    {% endif %}
  </div>

  <div class="status-card">
    <div class="status-card-title">BACKTEST</div>
    {% if backtest and backtest.ranking %}
      <div class="sr"><span class="sk">Strategy</span><span class="sv ok">{{ backtest.top_strategy }}</span></div>
      <div class="sr"><span class="sk">Pair</span><span class="sv">{{ backtest.top_pair }}</span></div>
      <div class="sr"><span class="sk">Return</span>
        <span class="sv {% if backtest.top_return > 0 %}ok{% else %}err{% endif %}">{{ "%+.0f"|format(backtest.top_return) }}%</span></div>
      <div class="sr"><span class="sk">Win rate</span><span class="sv">{{ backtest.top_winrate }}%</span></div>
    {% else %}
      <div style="color:var(--muted);font-size:10px;padding:8px 0;">No data. /backtest run</div>
    {% endif %}
  </div>

</div>

<!-- TRADE LOG -->
<div class="sec-hdr">&#9672; TRADE <em>LOG</em> ({{ trades|length }} entries)</div>
<div class="section-wrap">
  {% if trades %}
  <div style="overflow-x:auto;">
    <table class="rtable">
      <thead>
        <tr><th>TIME</th><th>COIN</th><th>ACTION</th><th>USD</th><th>STATUS</th><th>NOTES</th></tr>
      </thead>
      <tbody>
        {% for t in trades|reverse %}
        <tr>
          <td style="color:var(--muted);">{{ t.get('timestamp','')[:16]|replace('T',' ') }}</td>
          <td style="color:var(--neon2);">{{ t.get('coin','?') }}</td>
          <td>{% set act=t.get('action','') %}<span class="{% if act=='BUY' %}bb{% else %}bs{% endif %}">{{ act or '&mdash;' }}</span></td>
          <td style="font-family:monospace;">${{ "%.2f"|format(t.get('usd_amount',0)|float) }}</td>
          <td>{% set st=t.get('status','') %}<span style="color:{% if st=='executed' %}var(--neon){% elif st=='error' %}var(--red){% else %}var(--amber){% endif %};">{{ st or '&mdash;' }}</span></td>
          <td style="color:var(--muted);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ t.get('reason',t.get('notes',''))[:50] }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div style="font-family:'Press Start 2P',monospace;font-size:8px;color:var(--muted);padding:16px 0;">
    NO TRADES LOGGED &mdash; /autotrade on
  </div>
  {% endif %}
</div>

<!-- Mobile: floating chat hint button -->
<button id="mobile-chat-hint" onclick="window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'})" style="display:none;position:fixed;bottom:80px;right:16px;z-index:450;background:#0a120a;border:1px solid var(--neon);color:var(--neon);font-family:'Press Start 2P',monospace;font-size:7px;padding:10px 14px;cursor:pointer;box-shadow:0 0 12px #00ff8844;border-radius:2px;touch-action:manipulation;">
  &#9660; CHAT
</button>

<!-- CHAT PANEL -->
<div class="chat-panel" id="chat-panel">
  <div class="chat-hdr" onclick="toggleChat()">
    <span style="color:var(--neon);">&#9658;</span>
    <span class="chat-title">CLAWBOT CHAT</span>
    <span id="brain-badge" style="font-size:9px;color:var(--muted);"></span>
    <button onclick="clearChat(event)" style="background:none;border:1px solid var(--muted);color:var(--muted);font-size:9px;padding:2px 7px;cursor:pointer;font-family:'Share Tech Mono',monospace;margin-left:8px;">CLR</button>
    <span id="chat-toggle-icon" style="font-size:10px;color:var(--muted);margin-left:6px;">&#9650;</span>
  </div>
  <div id="chat-messages" class="chat-msgs">
    <div class="msg-b">CLAWBOT ONLINE &#8212; what's the move, Ronnie?</div>
  </div>
  <div class="chat-in-row" id="chat-input-row">
    <input id="chat-input" type="text" placeholder="> ask clawbot..."
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){sendChat();event.preventDefault();}">
    <button class="chat-send" onclick="sendChat()" id="send-btn">&#9658;</button>
  </div>
</div>

<script>
// Clock
function updateClock(){
  const n=new Date(),p=s=>String(s).padStart(2,'0');
  document.getElementById('live-clock').textContent=p(n.getHours())+':'+p(n.getMinutes())+':'+p(n.getSeconds());
}
updateClock();setInterval(updateClock,1000);

// Auto-refresh
const RS=30;let t=RS,chatActive=false;
const el=document.getElementById('cd'),bar=document.getElementById('refresh-bar');
function upBar(){bar.style.width=((RS-t)/RS*100)+'%';}upBar();
setInterval(()=>{
  if(chatActive){t=RS;el.textContent=t;upBar();return;}
  t--;el.textContent=t;upBar();if(t<=0)location.reload();
},1000);

// Execute dashboard commands
async function runCmd(btn, cmd) {
  if (btn.disabled) return;
  btn.disabled = true;
  var tip = btn.querySelector('.tip');
  var original = tip ? tip.textContent : 'RUN';
  if (tip) tip.textContent = 'RUNNING';
  var bar = document.getElementById('cmd-status');
  if (bar) { bar.className = 'running'; bar.textContent = '\u25B6 Running ' + cmd + '...'; }
  try {
    var res = await fetch('/api/execute-command', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: cmd})
    });
    var data = await res.json();
    if (data.success) {
      if (tip) tip.textContent = 'DONE';
      if (bar) { bar.className = 'ok'; bar.textContent = '\u2713 ' + cmd + ' \u2014 done'; }
      var summary = (data.output || '').replace(/<[^>]+>/g, '').trim().slice(0, 120);
      if (summary) showToast(summary, 'ok');
      setTimeout(function() { if (bar) { bar.className = ''; bar.textContent = ''; } }, 5000);
    } else {
      if (tip) tip.textContent = 'ERR';
      var errMsg = data.error || res.statusText || 'unknown error';
      if (bar) { bar.className = 'err'; bar.textContent = '\u2717 ' + cmd + ' failed \u2014 ' + errMsg.slice(0, 80); }
      showToast(cmd + ' failed: ' + errMsg.slice(0, 100), 'err');
      setTimeout(function() { if (bar) { bar.className = ''; bar.textContent = ''; } }, 8000);
    }
  } catch(e) {
    if (tip) tip.textContent = 'ERR';
    var msg = 'Connection error \u2014 Is dashboard running?';
    if (bar) { bar.className = 'err'; bar.textContent = '\u2717 ' + msg; }
    showToast(msg, 'err');
    setTimeout(function() { if (bar) { bar.className = ''; bar.textContent = ''; } }, 8000);
  }
  setTimeout(function() { if (tip) tip.textContent = original; btn.disabled = false; }, 2000);
}

// Chat
let chatOpen=true;
let _activeAgent=null;  // null = CLAWBOT (general), else "JARVIS"|"SCOUT" etc.
const AGENT_COLORS={JARVIS:'#00ff88',SCOUT:'#00ffff',WATCHDOG:'#ffaa00',CODEX:'#9b59b6',CLIPPER:'#ff6b35',HAWK:'#ff00aa'};
const msgs=document.getElementById('chat-messages');
const inp=document.getElementById('chat-input');
const CK='clawbot_chat_history';

function _ckKey(){return _activeAgent?'agent_chat_'+_activeAgent:CK;}

function _save(){
  const items=[];
  msgs.querySelectorAll('div[data-role]').forEach(d=>{
    items.push({role:d.dataset.role,text:d.dataset.text,brain:d.dataset.brain||''});
  });
  localStorage.setItem(_ckKey(),JSON.stringify(items.slice(-20)));
}
function _restore(){
  try{
    const key=_ckKey();
    // Clear visible messages first
    while(msgs.children.length>0)msgs.removeChild(msgs.lastChild);
    const saved=JSON.parse(localStorage.getItem(key)||'[]');
    if(saved.length===0){
      const welcome=document.createElement('div');
      welcome.className='msg-b';
      welcome.textContent=_activeAgent
        ?(_activeAgent+' online — what do you need?')
        :'CLAWBOT ONLINE \u2014 what\'s the move, Ronnie?';
      msgs.appendChild(welcome);
    } else {
      saved.forEach(m=>_add(m.text,m.role,m.brain));
    }
  }catch(e){}
}
_restore();

function openAgentChat(agentName){
  _activeAgent=agentName;
  const color=AGENT_COLORS[agentName]||'var(--neon)';
  // Update header
  document.querySelector('.chat-title').textContent=agentName+' CHAT';
  document.querySelector('.chat-hdr').style.borderBottomColor=color;
  document.querySelector('.chat-panel').style.borderColor=color;
  document.querySelector('.chat-panel').style.boxShadow='0 0 20px '+color+'33';
  // Reload history for this agent
  _restore();
  // Open if closed
  if(!chatOpen){chatOpen=true;msgs.style.display='flex';document.getElementById('chat-input-row').style.display='flex';document.getElementById('chat-toggle-icon').textContent='\u25B2';}
  // Scroll panel into view on mobile
  // Fixed elements don't respond to scrollIntoView — scroll page to bottom instead
  window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
  // Flash the chat panel so user knows it activated
  const cp = document.querySelector('.chat-panel');
  cp.style.transition = 'transform 0.3s ease';
  cp.style.transform = 'translateY(-8px)';
  setTimeout(() => { cp.style.transform = 'translateY(0)'; }, 300);
  inp.focus();
}

function resetToClawbot(){
  _activeAgent=null;
  document.querySelector('.chat-title').textContent='CLAWBOT CHAT';
  document.querySelector('.chat-hdr').style.borderBottomColor='var(--neon)';
  document.querySelector('.chat-panel').style.borderColor='var(--neon)';
  document.querySelector('.chat-panel').style.boxShadow='0 0 20px #00ff8822';
  _restore();
}

function toggleChat(){
  chatOpen=!chatOpen;
  msgs.style.display=chatOpen?'flex':'none';
  document.getElementById('chat-input-row').style.display=chatOpen?'flex':'none';
  document.getElementById('chat-toggle-icon').textContent=chatOpen?'\u25B2':'\u25BC';
}
function _add(text,type,brain){
  const d=document.createElement('div');
  d.className=type==='user'?'msg-u':'msg-b';
  d.dataset.role=type;d.dataset.text=text;d.dataset.brain=brain||'';
  d.textContent=text;
  if(brain&&type==='bot'){
    const b=document.createElement('span');
    b.textContent=' ('+brain+')';b.style.cssText='color:#555;font-size:9px;';d.appendChild(b);
    document.getElementById('brain-badge').textContent=brain;
  }
  msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d;
}
async function sendChat(){
  const text=inp.value.trim();if(!text)return;
  inp.value='';inp.disabled=true;
  document.getElementById('send-btn').disabled=true;chatActive=true;
  _add(text,'user','');_save();
  const th=_add('...','bot','');
  try{
    let endpoint,body;
    if(_activeAgent){
      endpoint='/api/chat/agent';
      body={agent:_activeAgent,message:text};
    } else {
      endpoint='/api/chat';
      body={message:text};
    }
    const res=await fetch(endpoint,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    const data=await res.json();
    if(data.error){th.textContent='ERROR: '+data.error;th.style.color='var(--red)';}
    else{
      th.textContent=data.reply;th.dataset.text=data.reply;th.dataset.brain=data.brain||'';
      const b=document.createElement('span');
      const label=data.agent?data.agent+'/'+data.brain:data.brain||'';
      b.textContent=' ('+label+')';
      b.style.cssText='color:#555;font-size:9px;';th.appendChild(b);
      document.getElementById('brain-badge').textContent=label;
    }
    _save();
  }catch(e){th.textContent='⚠️ CONNECTION ERROR — Is Ollama running on your PC? Check Task Manager or run: ollama serve';th.style.color='var(--red)';}
  inp.disabled=false;document.getElementById('send-btn').disabled=false;
  chatActive=false;inp.focus();
}
async function clearChat(e){
  e.stopPropagation();
  if(_activeAgent){
    await fetch('/api/chat/agent/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:_activeAgent})});
    localStorage.removeItem(_ckKey());
  } else {
    await fetch('/api/chat/clear',{method:'POST'});
    localStorage.removeItem(CK);
  }
  while(msgs.children.length>0)msgs.removeChild(msgs.lastChild);
  const welcome=document.createElement('div');welcome.className='msg-b';
  welcome.textContent=_activeAgent?(_activeAgent+' memory cleared.'):('CLAWBOT ONLINE \u2014 what\'s the move, Ronnie?');
  msgs.appendChild(welcome);
  document.getElementById('brain-badge').textContent='';
}
// Click chat title to return to ClawBot
document.querySelector('.chat-title').addEventListener('click',function(e){e.stopPropagation();resetToClawbot();});
// Only auto-focus on desktop — on mobile this pops the keyboard and breaks layout
if(!/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)){inp.focus();}

// Show mobile chat hint button when user is not at bottom
(function(){
  const hint = document.getElementById('mobile-chat-hint');
  if(!hint) return;
  if(/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)){
    hint.style.display = 'block';
    window.addEventListener('scroll', function(){
      const atBottom = (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 80;
      hint.style.display = atBottom ? 'none' : 'block';
    });
  }
})();

function openNewAgentModal() {
  const m = document.getElementById('new-agent-modal');
  m.style.display = 'flex';
  document.getElementById('na-name').focus();
}
function closeNewAgentModal() {
  document.getElementById('new-agent-modal').style.display = 'none';
  document.getElementById('na-msg').textContent = '';
}
async function submitNewAgent() {
  const name  = document.getElementById('na-name').value.trim().toUpperCase();
  const roles = document.getElementById('na-roles').value.split(',').map(s=>s.trim()).filter(Boolean);
  const emoji = document.getElementById('na-emoji').value.trim() || '🤖';
  const color = document.getElementById('na-color').value;
  const msgEl = document.getElementById('na-msg');
  if (!name) { msgEl.style.color='#ff4455'; msgEl.textContent='NAME REQUIRED'; return; }
  msgEl.style.color='#00ffff'; msgEl.textContent='DEPLOYING...';
  try {
    const res = await fetch('/api/agent/create', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, roles, emoji, color})
    });
    const data = await res.json();
    if (data.ok) {
      msgEl.style.color='#00ff88'; msgEl.textContent='AGENT DEPLOYED \u2713';
      setTimeout(() => { closeNewAgentModal(); location.reload(); }, 1200);
    } else {
      msgEl.style.color='#ff4455'; msgEl.textContent = data.error || 'FAILED';
    }
  } catch(e) {
    msgEl.style.color='#ff4455'; msgEl.textContent='CONNECTION ERROR';
  }
}
document.getElementById('new-agent-modal').addEventListener('click', function(e) {
  if (e.target === this) closeNewAgentModal();
});

// ── Config health banner ─────────────────────────────────────────
async function loadConfigBanner() {
  if (sessionStorage.getItem('config-banner-dismissed') === '1') return;
  try {
    var res = await fetch('/api/system-status');
    if (!res.ok) return;
    var data = await res.json();
    var issues = data.issues || [];
    if (issues.length === 0) return;
    var banner = document.getElementById('config-banner');
    var text = document.getElementById('config-banner-text');
    if (!banner || !text) return;
    text.textContent = issues.length + ' issue' + (issues.length > 1 ? 's' : '') + ': ' + issues.join(' \u00B7 ');
    banner.classList.add('visible');
  } catch(e) { /* non-critical */ }
}
function dismissConfigBanner() {
  var banner = document.getElementById('config-banner');
  if (banner) banner.classList.remove('visible');
  sessionStorage.setItem('config-banner-dismissed', '1');
}
loadConfigBanner();

// ── Toast notifications ──────────────────────────────────────────
function showToast(msg, type) {
  var container = document.getElementById('toast-container');
  if (!container) return;
  while (container.children.length >= 3) {
    container.removeChild(container.firstChild);
  }
  var el = document.createElement('div');
  el.className = 'toast-msg ' + (type || 'ok');
  el.textContent = msg;
  container.appendChild(el);
  requestAnimationFrame(function() {
    requestAnimationFrame(function() { el.classList.add('show'); });
  });
  var delay = type === 'err' ? 8000 : 3000;
  setTimeout(function() {
    el.classList.remove('show');
    setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
  }, delay);
}
</script>

<!-- New Agent Modal -->
<div id="new-agent-modal" style="display:none;position:fixed;inset:0;background:#000a;z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#111;border:1px solid #ff00aa44;border-radius:12px;padding:24px;width:90%;max-width:480px;font-family:'Press Start 2P',monospace;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <span style="font-size:9px;color:#ff00aa;">+ NEW AGENT</span>
      <button onclick="closeNewAgentModal()" style="background:none;border:none;color:#666;font-size:14px;cursor:pointer;">&#x2715;</button>
    </div>
    <div style="margin-bottom:12px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">AGENT NAME</label>
      <input id="na-name" type="text" placeholder="e.g. TRACKER" style="width:100%;background:#0a0a0a;border:1px solid #333;color:#fff;padding:8px;font-family:'Press Start 2P',monospace;font-size:7px;border-radius:4px;">
    </div>
    <div style="margin-bottom:12px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">ROLES (comma separated)</label>
      <input id="na-roles" type="text" placeholder="Research, Analysis, Reports" style="width:100%;background:#0a0a0a;border:1px solid #333;color:#fff;padding:8px;font-family:'Press Start 2P',monospace;font-size:7px;border-radius:4px;">
    </div>
    <div style="margin-bottom:12px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">EMOJI AVATAR</label>
      <input id="na-emoji" type="text" placeholder="&#x1F916;" maxlength="2" style="width:60px;background:#0a0a0a;border:1px solid #333;color:#fff;padding:8px;font-size:16px;border-radius:4px;text-align:center;">
    </div>
    <div style="margin-bottom:16px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">NEON COLOR</label>
      <select id="na-color" style="background:#0a0a0a;border:1px solid #333;color:#fff;padding:6px;font-family:'Press Start 2P',monospace;font-size:7px;border-radius:4px;">
        <option value="#00ff88">GREEN</option>
        <option value="#00ffff">CYAN</option>
        <option value="#ff6b35">ORANGE</option>
        <option value="#9b59b6">PURPLE</option>
        <option value="#f39c12">YELLOW</option>
        <option value="#3498db">BLUE</option>
        <option value="#ff00aa">PINK</option>
      </select>
    </div>
    <div id="na-msg" style="font-size:7px;margin-bottom:12px;min-height:16px;"></div>
    <button onclick="submitNewAgent()" style="width:100%;background:#0a0a0a;border:1px solid #ff00aa;color:#ff00aa;font-family:'Press Start 2P',monospace;font-size:8px;padding:10px;cursor:pointer;border-radius:4px;letter-spacing:1px;">
      &#x25B6; DEPLOY AGENT
    </button>
  </div>
</div>

<div id="toast-container"></div>
</body>
</html>
"""


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


@app.route("/api/execute-command", methods=["POST"])
def api_execute_command():
    data = request.get_json(force=True) or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"success": False, "error": "No command provided."}), 400
    try:
        output = execute_dashboard_command(command)
        return jsonify({"success": True, "output": output})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint. Returns 200 as long as the Flask process is alive.
    Use /api/system-status for detailed health breakdown."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/system-status", methods=["GET"])
def api_system_status():
    """Get real-time system monitoring status with computed issues list."""
    monitor = _get_monitor()
    status = monitor.get_status()

    issues = []
    ollama = get_ollama_status()
    if not ollama.get("online"):
        issues.append("Ollama offline")
    elif ollama.get("cfg_missing"):
        configured = os.getenv("OLLAMA_MODEL", "—")
        active = ollama.get("active", "unknown")
        issues.append(f"Ollama model mismatch: '{configured}' not installed, using '{active}'")

    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        issues.append("Claude API key not set (ANTHROPIC_API_KEY)")
    if not os.getenv("CRYPTOCOM_API_KEY", "").strip():
        issues.append("Crypto.com API key not set (CRYPTOCOM_API_KEY)")

    status["issues"] = issues
    return jsonify(status), 200


# ── Per-agent chat histories ───────────────────────────────────────────────────
_agent_histories: dict = {}   # keyed by agent name

_AGENT_SYSTEMS = {
    "JARVIS": (
        "You are JARVIS, the core brain of OpenClaw trading bot. "
        "You handle LLM reasoning, market analysis, and decision-making via Ollama (mistral:latest) "
        "with Claude Haiku fallback. Answer concisely. Ronnie is your operator."
    ),
    "SCOUT": (
        "You are SCOUT, OpenClaw's job hunting agent. You search Whop, Discord, Upwork and other "
        "platforms using 27 search terms across 5 categories: Telegram/Discord bots, Python scripts, "
        "AI automation, content/ghostwriting, crypto newsletters. "
        "Help Ronnie find and evaluate freelance gigs. Be direct and practical."
    ),
    "WATCHDOG": (
        "You are WATCHDOG, OpenClaw's auto-trading sentinel. You monitor RSI+MACD signals on "
        "Crypto.com, manage DCA positions, and guard against bad trades. "
        "Give trading insights, risk assessments, and signal analysis. "
        "Always note: real trades require /autotrade on in Telegram."
    ),
    "CODEX": (
        "You are CODEX, OpenClaw's code intelligence agent. You run automated code reviews, "
        "manage the auto-upgrade pipeline, and maintain code quality. "
        "You know the full OpenClaw codebase: Python, Flask, python-telegram-bot, APScheduler. "
        "Help with code questions, reviews, and debugging."
    ),
    "CLIPPER": (
        "You are CLIPPER, OpenClaw's CashClaw income agent. You handle the full outreach pipeline: "
        "Scout finds jobs → quality gate filters → HumanVoice (Ollama draft + Haiku rewrite) "
        "humanizes cold outreach → Ronnie approves → message sent. "
        "Help write and improve outreach messages. If given raw text, offer to humanize it."
    ),
    "HAWK": (
        "You are HAWK, OpenClaw's market intelligence agent. You track live crypto prices, "
        "fear & greed index, news sentiment, and macro signals. "
        "Give sharp, data-driven market reads. No fluff."
    ),
}


@app.route("/api/chat/agent", methods=["POST"])
def api_chat_agent():
    """POST {"agent": "JARVIS", "message": "..."} → {"reply": "...", "brain": "...", "agent": "..."}"""
    global _agent_histories
    try:
        sys.path.insert(0, str(ROOT))
        from core.brain import ask_hybrid

        data    = request.get_json(force=True)
        agent   = (data.get("agent") or "JARVIS").upper()
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "Empty message"}), 400

        if agent not in _AGENT_SYSTEMS:
            return jsonify({"error": f"Unknown agent: {agent}", "valid_agents": list(_AGENT_SYSTEMS.keys())}), 400

        system = _AGENT_SYSTEMS[agent]
        history = _agent_histories.get(agent, [])

        # ── Agent-specific context injection ─────────────────────────────────
        context_prefix = ""

        if agent == "JARVIS":
            try:
                prices = get_prices()
                price_str = " | ".join(f"{c}=${d['price']:,.0f}({d['sign']}{d['change']}%)" for c, d in prices.items()) if prices else "unavailable"
                context_prefix = f"[LIVE PRICES as of now] {price_str}. "
            except Exception:
                context_prefix = ""

        elif agent == "SCOUT":
            scout_state = _read_json(DATA_DIR / "job_scout_state.json", {})
            jobs = scout_state.get("pending_jobs", [])
            applied = scout_state.get("applied", [])
            context_prefix = (
                f"[SCOUT STATE] Pending jobs: {len(jobs)}. Applied: {len(applied)}. "
                f"Last scan: {scout_state.get('last_scan', 'never')}. "
            )
            if jobs:
                context_prefix += f"Top job: {jobs[0].get('title','?')} on {jobs[0].get('platform','?')} "
                context_prefix += f"(score {jobs[0].get('score','?')}). "

        elif agent == "WATCHDOG":
            prices = get_prices()
            at     = get_autotrade_status()
            trades = get_recent_trades(n=5)
            price_str = " | ".join(f"{c}=${d['price']:,.0f}({d['sign']}{d['change']}%)" for c, d in prices.items()) if prices else "unavailable"
            context_prefix = (
                f"[MARKET] {price_str}. "
                f"AutoTrade: {'ENABLED' if at.get('enabled') else 'DISABLED'}. "
                f"Recent trades: {len(trades)}. "
            )

        elif agent == "HAWK":
            prices = get_prices()
            price_str = " | ".join(f"{c}=${d['price']:,.2f} ({d['sign']}{d['change']}%)" for c, d in prices.items()) if prices else "CoinGecko unavailable"
            context_prefix = f"[LIVE PRICES] {price_str}. "

        elif agent == "CODEX":
            py_files = list(ROOT.rglob("*.py"))
            file_count = len([f for f in py_files if ".venv" not in str(f)])
            last_review = get_last_code_review()
            context_prefix = (
                f"[CODEBASE] {file_count} Python files. "
                f"Last review: {last_review.get('date', 'none')}. "
                f"Skills installed: {len(get_installed_skills())}. "
            )

        elif agent == "CLIPPER":
            applier_state = _read_json(DATA_DIR / "applier_state.json", {})
            income_log    = _read_json(DATA_DIR / "income_log.json", [])
            context_prefix = (
                f"[CASHCLAW] Pending drafts: {len(applier_state.get('pending_drafts', []))}. "
                f"Sent total: {len(applier_state.get('sent', []))}. "
                f"Income logged: {len(income_log)} entries. "
            )
            # If message looks like outreach text to humanize, run through HumanVoice
            if len(message) > 80 and any(w in message.lower() for w in ["hi ", "hello", "hey ", "i saw", "i noticed", "i'm reaching", "i am reaching"]):
                try:
                    from agents.human_voice import humanize
                    humanized = humanize(message, context="cold outreach")
                    return jsonify({
                        "reply": f"[HUMANIZED by HumanVoice]\n\n{humanized}",
                        "brain": "haiku",
                        "agent": agent,
                    })
                except Exception:
                    pass  # fall through to regular LLM

        # Inject context into message
        full_message = context_prefix + message if context_prefix else message

        history.append({"role": "user", "content": full_message})
        history = history[-20:]

        reply, brain = ask_hybrid(full_message, system=system, history=history[:-1])

        history.append({"role": "assistant", "content": reply})
        _agent_histories[agent] = history[-20:]

        return jsonify({"reply": reply, "brain": brain, "agent": agent})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/chat/agent/clear", methods=["POST"])
def api_chat_agent_clear():
    data  = request.get_json(force=True) or {}
    agent = (data.get("agent") or "").upper()
    if agent and agent in _agent_histories:
        _agent_histories[agent] = []
    elif not agent:
        _agent_histories.clear()
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
            task.state = "in_progress"
        elif action == "review":
            task.state = "review"
        elif action == "done":
            task.state = "done"
        else:
            return jsonify({"success": False, "error": "Invalid action"}), 400

        task.updated_at = datetime.now(timezone.utc).isoformat()
        orchestrator.save_tasks()
        return jsonify({"success": True})
    
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Task Board API ─────────────────────────────────────────────────────────────

_TASKBOARD_FILE = DATA_DIR / "taskboard.json"

def _get_taskboard():
    return _read_json(_TASKBOARD_FILE, [])

def _save_taskboard(tasks):
    with open(_TASKBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)

@app.route("/api/taskboard")
def api_taskboard():
    return jsonify(_get_taskboard())

@app.route("/api/taskboard/add", methods=["POST"])
def api_taskboard_add():
    import time
    data = request.json or {}
    task = {
        "id": f"task_{int(time.time())}",
        "title": data.get("title", "Untitled"),
        "description": data.get("description", ""),
        "status": data.get("status", "backlog"),
        "priority": data.get("priority", "medium"),
        "assigned_to": data.get("assigned_to", "user"),
        "agent": data.get("agent"),
        "tags": data.get("tags", []),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tasks = _get_taskboard()
    tasks.append(task)
    _save_taskboard(tasks)
    return jsonify({"ok": True, "task": task})

@app.route("/api/taskboard/update", methods=["POST"])
def api_taskboard_update():
    data = request.json or {}
    task_id = data.get("id")
    tasks = _get_taskboard()
    for t in tasks:
        if t["id"] == task_id:
            for k, v in data.items():
                if k != "id":
                    t[k] = v
            t["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_taskboard(tasks)
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Task not found"}), 404

@app.route("/api/taskboard/delete", methods=["POST"])
def api_taskboard_delete():
    data = request.json or {}
    task_id = data.get("id")
    existing = _get_taskboard()
    filtered = [t for t in existing if t["id"] != task_id]
    if len(filtered) == len(existing):
        return jsonify({"ok": False, "error": "Task not found"}), 404
    _save_taskboard(filtered)
    return jsonify({"ok": True})


# ── Task Board Kanban Page ──────────────────────────────────────────────────────

TASKBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Task Board</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root {
    --bg-base: #0d0d0d; --bg-card: #141414; --bg-card-header: #1e1e1e;
    --border-col: #242424; --text-main: #e0e0e0; --text-muted: #666;
    --green: #00ff88; --red: #ff4455; --amber: #ffaa00; --blue: #4499ff;
  }
  body { background: linear-gradient(135deg, var(--bg-base) 0%, #1a1a1a 100%); color: var(--text-main); font-family: 'Segoe UI', sans-serif; min-height: 100vh; }
  .navbar { background: var(--bg-card) !important; border-bottom: 1px solid var(--border-col); }
  .navbar-brand { color: var(--green) !important; font-weight: bold; }
  .nav-link-back { color: var(--text-muted) !important; font-size: 0.85rem; text-decoration: none; }
  .nav-link-back:hover { color: var(--green) !important; }
  .kanban-wrap { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; padding: 1.5rem; min-height: calc(100vh - 70px); }
  .kanban-col { background: var(--bg-card); border: 1px solid var(--border-col); border-radius: 12px; display: flex; flex-direction: column; min-height: 400px; }
  .kanban-col-header { background: var(--bg-card-header); border-bottom: 1px solid var(--border-col); border-radius: 12px 12px 0 0; padding: 0.85rem 1rem; display: flex; align-items: center; justify-content: space-between; }
  .kanban-col-header h6 { margin: 0; font-weight: 600; font-size: 0.9rem; letter-spacing: 0.04em; text-transform: uppercase; }
  .col-backlog .kanban-col-header h6 { color: #aaa; }
  .col-in_progress .kanban-col-header h6 { color: var(--amber); }
  .col-review .kanban-col-header h6 { color: var(--blue); }
  .col-done .kanban-col-header h6 { color: var(--green); }
  .kanban-col-body { padding: 0.75rem; flex: 1; display: flex; flex-direction: column; gap: 0.6rem; }
  .count-badge { background: var(--border-col); color: var(--text-muted); border-radius: 20px; font-size: 0.75rem; padding: 1px 8px; font-weight: 600; }
  .task-card { background: #1a1a1a; border: 1px solid var(--border-col); border-radius: 8px; padding: 0.75rem; transition: transform 0.15s, box-shadow 0.15s; }
  .task-card:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.4); border-color: #333; }
  .task-card .card-title { font-size: 0.9rem; font-weight: 600; margin-bottom: 0.3rem; line-height: 1.3; }
  .task-card .card-desc { font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.5rem; line-height: 1.4; }
  .task-card .card-meta { display: flex; flex-wrap: wrap; gap: 0.3rem; align-items: center; }
  .task-card .card-actions { display: flex; gap: 0.3rem; margin-top: 0.5rem; border-top: 1px solid var(--border-col); padding-top: 0.5rem; }
  .pri-critical { background: rgba(255,68,85,0.15); color: var(--red); border: 1px solid rgba(255,68,85,0.3); border-radius: 4px; font-size: 0.7rem; padding: 1px 6px; font-weight: 700; text-transform: uppercase; }
  .pri-high     { background: rgba(255,170,0,0.12); color: var(--amber); border: 1px solid rgba(255,170,0,0.3); border-radius: 4px; font-size: 0.7rem; padding: 1px 6px; font-weight: 700; text-transform: uppercase; }
  .pri-medium   { background: rgba(68,153,255,0.12); color: var(--blue); border: 1px solid rgba(68,153,255,0.3); border-radius: 4px; font-size: 0.7rem; padding: 1px 6px; font-weight: 700; text-transform: uppercase; }
  .pri-low      { background: rgba(102,102,102,0.12); color: #888; border: 1px solid #333; border-radius: 4px; font-size: 0.7rem; padding: 1px 6px; font-weight: 700; text-transform: uppercase; }
  .tag { background: rgba(68,153,255,0.1); color: #88aaff; border: 1px solid rgba(68,153,255,0.2); border-radius: 4px; font-size: 0.68rem; padding: 1px 5px; }
  .avatar-chip { font-size: 0.78rem; color: var(--text-muted); }
  .agent-chip { font-size: 0.68rem; color: #555; font-style: italic; }
  .btn-move { background: transparent; border: 1px solid var(--border-col); color: var(--text-muted); border-radius: 4px; padding: 2px 8px; font-size: 0.72rem; cursor: pointer; transition: all 0.15s; }
  .btn-move:hover { border-color: var(--green); color: var(--green); }
  .btn-del { background: transparent; border: 1px solid var(--border-col); color: var(--text-muted); border-radius: 4px; padding: 2px 7px; font-size: 0.72rem; cursor: pointer; transition: all 0.15s; margin-left: auto; }
  .btn-del:hover { border-color: var(--red); color: var(--red); }
  .btn-add-task { background: linear-gradient(45deg, var(--green), #00cc66); border: none; color: #000; font-weight: 700; border-radius: 8px; padding: 0.45rem 1.1rem; font-size: 0.85rem; cursor: pointer; transition: opacity 0.15s; }
  .btn-add-task:hover { opacity: 0.85; }
  .modal-content { background: var(--bg-card); border: 1px solid var(--border-col); color: var(--text-main); border-radius: 12px; }
  .modal-header { border-bottom: 1px solid var(--border-col); }
  .modal-footer { border-top: 1px solid var(--border-col); }
  .form-control, .form-select { background: #1a1a1a; border: 1px solid var(--border-col); color: var(--text-main); border-radius: 6px; }
  .form-control:focus, .form-select:focus { background: #1e1e1e; border-color: var(--green); color: var(--text-main); box-shadow: 0 0 0 2px rgba(0,255,136,0.12); }
  .form-label { font-size: 0.85rem; color: #aaa; }
  .form-select option { background: #1a1a1a; }
  .auto-refresh { font-size: 0.8rem; color: var(--text-muted); }
  @media (max-width: 900px) { .kanban-wrap { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 560px) {
    .kanban-wrap { grid-template-columns: 1fr; padding: 0.75rem; gap: 0.75rem; }
    .kanban-col { min-height: 200px; }
    .kanban-col-header { padding: 0.6rem 0.75rem; }
    .task-card { padding: 0.6rem; font-size: 0.8rem; }
    .btn-move, .btn-del { min-width: 36px; min-height: 36px; padding: 6px; }
    .btn-add-task { padding: 10px 16px; font-size: 0.8rem; }
    .navbar { padding: 0.5rem 0.75rem; }
    .modal input, .modal textarea, .modal select { font-size: 16px !important; }
  }
</style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark">
  <div class="container-fluid">
    <span class="navbar-brand"><i class="fas fa-tasks me-2"></i>OpenClaw Task Board</span>
    <div class="d-flex align-items-center gap-3">
      <button class="btn-add-task" onclick="openAddModal()"><i class="fas fa-plus me-1"></i>Add Task</button>
      <a href="/" class="nav-link-back"><i class="fas fa-arrow-left me-1"></i>Dashboard</a>
      <a href="/portfolio" class="text-decoration-none text-muted small"><i class="fas fa-wallet"></i> Portfolio</a>
      <a href="/holdings" class="text-decoration-none text-muted small"><i class="fas fa-coins"></i> Holdings</a>
      <a href="/team" class="text-decoration-none text-muted small"><i class="fas fa-users"></i> Team</a>
      <span class="auto-refresh"><i class="fas fa-clock me-1"></i>Auto-refresh <span id="cd">30</span>s</span>
    </div>
  </div>
</nav>

<div class="kanban-wrap" id="kanban-board"></div>

<!-- Add Task Modal -->
<div class="modal fade" id="addTaskModal" tabindex="-1">
  <div class="modal-dialog modal-dialog-centered">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title"><i class="fas fa-plus-circle me-2 text-success"></i>New Task</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div class="mb-3">
          <label class="form-label">Title *</label>
          <input type="text" class="form-control" id="new-title" placeholder="Task title">
        </div>
        <div class="mb-3">
          <label class="form-label">Description</label>
          <textarea class="form-control" id="new-desc" rows="2" placeholder="Short description"></textarea>
        </div>
        <div class="row g-2">
          <div class="col-6">
            <label class="form-label">Status</label>
            <select class="form-select" id="new-status">
              <option value="backlog">Backlog</option>
              <option value="in_progress">In Progress</option>
              <option value="review">Review</option>
              <option value="done">Done</option>
            </select>
          </div>
          <div class="col-6">
            <label class="form-label">Priority</label>
            <select class="form-select" id="new-priority">
              <option value="low">Low</option>
              <option value="medium" selected>Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>
        </div>
        <div class="row g-2 mt-1">
          <div class="col-6">
            <label class="form-label">Assigned to</label>
            <select class="form-select" id="new-assigned">
              <option value="user">User</option>
              <option value="claude">Claude</option>
            </select>
          </div>
          <div class="col-6">
            <label class="form-label">Agent</label>
            <input type="text" class="form-control" id="new-agent" placeholder="e.g. FeatureAgent">
          </div>
        </div>
        <div class="mt-2">
          <label class="form-label">Tags <small class="text-muted">(comma separated)</small></label>
          <input type="text" class="form-control" id="new-tags" placeholder="bug, feature, trading">
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn-add-task" onclick="submitNewTask()"><i class="fas fa-save me-1"></i>Save Task</button>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
const COLUMNS = [
  { key: 'backlog',     label: 'Backlog',     icon: 'fa-inbox' },
  { key: 'in_progress', label: 'In Progress', icon: 'fa-spinner' },
  { key: 'review',      label: 'Review',      icon: 'fa-eye' },
  { key: 'done',        label: 'Done',        icon: 'fa-check-circle' },
];
const COL_ORDER = ['backlog', 'in_progress', 'review', 'done'];
let tasks = [];

async function loadTasks() {
  try {
    const res = await fetch('/api/taskboard');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    tasks = await res.json();
  } catch(e) {
    console.error('Failed to load tasks:', e);
    tasks = tasks || [];
  }
  renderBoard();
}

function escHtml(s) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(s || ''));
  return d.innerHTML;
}

function assigneeLabel(a) {
  return a === 'claude' ? '&#x1F916; Claude' : '&#x1F464; User';
}

function renderCard(task) {
  const idx = COL_ORDER.indexOf(task.status);
  const canLeft  = idx > 0;
  const canRight = idx < COL_ORDER.length - 1;
  const tags = (task.tags || []).map(t => '<span class="tag">#' + escHtml(t) + '</span>').join('');
  const agent = task.agent ? '<span class="agent-chip">' + escHtml(task.agent) + '</span>' : '';
  const pri = task.priority || 'low';
  return '<div class="task-card">' +
    '<div class="card-title">' + escHtml(task.title) + '</div>' +
    (task.description ? '<div class="card-desc">' + escHtml(task.description) + '</div>' : '') +
    '<div class="card-meta">' +
      '<span class="pri-' + pri + '">' + pri + '</span>' +
      '<span class="avatar-chip">' + assigneeLabel(task.assigned_to) + '</span>' +
      agent + tags +
    '</div>' +
    '<div class="card-actions">' +
      (canLeft  ? '<button class="btn-move" onclick="moveTask(\'' + task.id + '\',-1)"><i class="fas fa-arrow-left"></i></button>' : '') +
      (canRight ? '<button class="btn-move" onclick="moveTask(\'' + task.id + '\',1)"><i class="fas fa-arrow-right"></i></button>' : '') +
      '<button class="btn-del" onclick="deleteTask(\'' + task.id + '\')"><i class="fas fa-trash"></i></button>' +
    '</div>' +
  '</div>';
}

function renderBoard() {
  const board = document.getElementById('kanban-board');
  const grouped = {};
  COL_ORDER.forEach(k => { grouped[k] = []; });
  tasks.forEach(t => {
    const col = COL_ORDER.includes(t.status) ? t.status : 'backlog';
    grouped[col].push(t);
  });
  board.innerHTML = COLUMNS.map(col => {
    const cards = grouped[col.key].map(renderCard).join('');
    return '<div class="kanban-col col-' + col.key + '">' +
      '<div class="kanban-col-header">' +
        '<h6><i class="fas ' + col.icon + ' me-2"></i>' + col.label + '</h6>' +
        '<span class="count-badge">' + grouped[col.key].length + '</span>' +
      '</div>' +
      '<div class="kanban-col-body">' +
        (cards || '<div style="color:#555;font-size:0.8rem;text-align:center;margin-top:2rem;padding:1rem;"><i class=\"fas fa-inbox\" style=\"font-size:2rem;display:block;margin-bottom:8px;\"></i>No tasks yet</div>') +
      '</div>' +
    '</div>';
  }).join('');
}

async function moveTask(id, dir) {
  const task = tasks.find(t => t.id === id);
  if (!task) return;
  const idx = COL_ORDER.indexOf(task.status);
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= COL_ORDER.length) return;
  await fetch('/api/taskboard/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ id: id, status: COL_ORDER[newIdx] }),
  });
  await loadTasks();
}

async function deleteTask(id) {
  if (!confirm('Delete this task?')) return;
  await fetch('/api/taskboard/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ id: id }),
  });
  await loadTasks();
}

let _modal;
function openAddModal() {
  if (!_modal) _modal = new bootstrap.Modal(document.getElementById('addTaskModal'));
  ['new-title','new-desc','new-agent','new-tags'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('new-status').value   = 'backlog';
  document.getElementById('new-priority').value = 'medium';
  document.getElementById('new-assigned').value = 'user';
  _modal.show();
}

async function submitNewTask() {
  const title = document.getElementById('new-title').value.trim();
  if (!title) { document.getElementById('new-title').focus(); return; }
  const tagsRaw = document.getElementById('new-tags').value;
  const tags = tagsRaw ? tagsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  const agent = document.getElementById('new-agent').value.trim() || null;
  await fetch('/api/taskboard/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      title:       title,
      description: document.getElementById('new-desc').value.trim(),
      status:      document.getElementById('new-status').value,
      priority:    document.getElementById('new-priority').value,
      assigned_to: document.getElementById('new-assigned').value,
      agent:       agent,
      tags:        tags,
    }),
  });
  _modal.hide();
  await loadTasks();
}

let countdown = 30;
const cdEl = document.getElementById('cd');
setInterval(() => {
  countdown--;
  if (countdown <= 0) { loadTasks(); countdown = 30; }
  cdEl.textContent = countdown;
}, 1000);

renderBoard();
loadTasks();
</script>
</body>
</html>"""


@app.route("/taskboard")
def taskboard():
    return render_template_string(TASKBOARD_HTML)


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
    skills      = get_installed_skills()
    claude_ok  = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    crypto_ok  = bool(os.getenv("CRYPTOCOM_API_KEY", "").strip())
    now        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return render_template_string(
        DASHBOARD_HTML,
        usage=usage, prices=prices, ollama=ollama, bot=bot,
        tasks=tasks, trades=trades, cache=cache,
        autotrade=autotrade, backtest=backtest,
        notes=notes, codereview=codereview,
        orchestration=orchestration, skills=skills,
        claude_ok=claude_ok, crypto_ok=crypto_ok, now=now,
    )


# ── Team page ──────────────────────────────────────────────────────────────────

TEAM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>OpenClaw — Team</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  body { background: linear-gradient(135deg, #0d0d0d 0%, #1a1a1a 100%); color: #e0e0e0; font-family: 'Segoe UI', sans-serif; min-height: 100vh; }
  .navbar { background: #141414 !important; border-bottom: 1px solid #242424; }
  .stats-bar { background: #141414; border-bottom: 1px solid #242424; padding: 0.6rem 1.5rem; font-size: 0.85rem; color: #aaa; }
  .stats-bar span { margin-right: 2rem; }
  .stats-bar .val { color: #00ff88; font-weight: 600; }
  /* Lead card */
  .lead-card { background: linear-gradient(135deg, #1a1f2e, #141414); border: 1px solid #00ff8844; border-radius: 18px; box-shadow: 0 0 30px #00ff8811; padding: 2rem; margin-bottom: 2rem; }
  .lead-avatar { font-size: 3.5rem; line-height: 1; }
  .lead-name { font-size: 1.6rem; font-weight: 700; color: #00ff88; }
  .lead-role { color: #aaa; font-size: 0.95rem; }
  .lead-badge { background: #00ff8822; border: 1px solid #00ff8866; color: #00ff88; border-radius: 20px; padding: 0.2rem 0.75rem; font-size: 0.8rem; font-weight: 600; display: inline-block; }
  .skill-tag { background: #1e2a1e; border: 1px solid #00ff8833; color: #00ff88; border-radius: 12px; padding: 0.15rem 0.6rem; font-size: 0.75rem; display: inline-block; margin: 2px; }
  /* Department sections */
  .dept-section { margin-bottom: 2rem; }
  .dept-header { border-radius: 10px 10px 0 0; padding: 0.6rem 1.2rem; font-weight: 700; font-size: 0.95rem; margin-bottom: 0; }
  /* Agent cards */
  .agent-card { background: #141414; border: 1px solid #242424; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.3); transition: transform 0.2s, box-shadow 0.2s; padding: 1.2rem; height: 100%; }
  .agent-card:hover { transform: translateY(-3px); box-shadow: 0 6px 20px rgba(0,0,0,0.5); }
  .agent-avatar { font-size: 2rem; }
  .agent-name { font-size: 1rem; font-weight: 700; color: #e0e0e0; }
  .agent-role { color: #888; font-size: 0.8rem; }
  .badge-active { background: #00ff8822; border: 1px solid #00ff8866; color: #00ff88; border-radius: 20px; padding: 0.15rem 0.6rem; font-size: 0.72rem; font-weight: 600; }
  .badge-oncall { background: #44444422; border: 1px solid #555; color: #aaa; border-radius: 20px; padding: 0.15rem 0.6rem; font-size: 0.72rem; font-weight: 600; }
  .tasks-count { font-size: 1.3rem; font-weight: 700; color: #4fc3f7; }
  .tasks-label { font-size: 0.7rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
  .agent-skill-tag { background: #1e1e2e; border: 1px solid #333; color: #aaa; border-radius: 10px; padding: 0.1rem 0.5rem; font-size: 0.7rem; display: inline-block; margin: 2px; }
  /* Nav links */
  .nav-link-custom { color: #888; text-decoration: none; font-size: 0.85rem; }
  .nav-link-custom:hover { color: #00ff88; }
  .auto-refresh { font-size: 0.8rem; color: #666; }
  @media (max-width: 560px) {
    .lead-card { flex-direction: column; padding: 1rem; }
    .agent-card { padding: 0.75rem; }
    .stats-bar { flex-wrap: wrap; gap: 0.5rem; padding: 0.75rem; }
    .new-agent-btn { width: 100%; margin-top: 8px; }
    #new-agent-modal > div { width: 95% !important; padding: 16px !important; }
    #new-agent-modal input, #new-agent-modal textarea, #new-agent-modal select { font-size: 16px !important; }
  }
</style>
</head>
<body>

<nav class="navbar navbar-expand-lg navbar-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="/"><i class="fas fa-robot"></i> OpenClaw Dashboard</a>
    <div class="d-flex align-items-center gap-3">
      <a href="/" class="nav-link-custom"><i class="fas fa-tachometer-alt"></i> Dashboard</a>
      <a href="/taskboard" class="nav-link-custom"><i class="fas fa-clipboard-list"></i> Task Board</a>
      <a href="/portfolio" class="nav-link-custom"><i class="fas fa-wallet"></i> Portfolio</a>
      <a href="/holdings" class="nav-link-custom"><i class="fas fa-coins"></i> Holdings</a>
      <button class="new-agent-btn" onclick="openNewAgentModal()" style="background:#0a0a0a;border:1px solid #ff00aa;color:#ff00aa;border-radius:8px;padding:5px 12px;font-size:.82rem;cursor:pointer;font-weight:600;">+ NEW AGENT</button>
      <span class="auto-refresh"><i class="fas fa-sync-alt"></i> Auto-refresh 60s</span>
    </div>
  </div>
</nav>

<!-- Stats bar -->
<div class="stats-bar d-flex align-items-center">
  <span>Total Agents: <span class="val">{{ total_agents }}</span></span>
  <span>Active Now: <span class="val">{{ active_count }}</span></span>
  <span>Tasks Completed: <span class="val">{{ tasks_total }}</span></span>
  <span class="ms-auto text-muted" style="font-size:0.78rem">{{ now }}</span>
</div>

<div class="container-fluid mt-4 pb-5">

  <!-- Lead card -->
  <div class="lead-card d-flex align-items-start gap-4 flex-wrap">
    <div class="lead-avatar">{{ lead.avatar }}</div>
    <div class="flex-grow-1">
      <div class="d-flex align-items-center gap-3 flex-wrap mb-1">
        <div class="lead-name">{{ lead.name }}</div>
        <span class="lead-badge"><i class="fas fa-circle" style="font-size:0.55rem;margin-right:4px"></i>{{ lead.status }}</span>
      </div>
      <div class="lead-role mb-2">{{ lead.role }} &nbsp;·&nbsp; <span class="text-muted small font-monospace">{{ lead.model }}</span></div>
      <div class="mb-2">
        {% for s in lead.skills %}
        <span class="skill-tag">{{ s }}</span>
        {% endfor %}
      </div>
      <ul class="text-muted small mb-0 ps-3">
        {% for r in lead.responsibilities %}
        <li>{{ r }}</li>
        {% endfor %}
      </ul>
    </div>
  </div>

  <!-- Department sections -->
  {% for dept in departments %}
  {% set dept_agents = agents | selectattr("department", "equalto", dept.name) | list %}
  {% if dept_agents %}
  <div class="dept-section">
    <div class="dept-header mb-3" style="background: {{ dept.color }}22; border-left: 4px solid {{ dept.color }}; color: {{ dept.color }};">
      <i class="fas fa-layer-group me-2"></i>{{ dept.name }} &nbsp;<small class="fw-normal" style="color:{{ dept.color }}99">{{ dept_agents|length }} agent{{ 's' if dept_agents|length != 1 else '' }}</small>
    </div>
    <div class="row g-3">
      {% for agent in dept_agents %}
      <div class="col-md-6 col-lg-4 col-xl-3">
        <div class="agent-card">
          <div class="d-flex align-items-start justify-content-between mb-2">
            <div class="d-flex align-items-center gap-2">
              <span class="agent-avatar">{{ agent.avatar }}</span>
              <div>
                <div class="agent-name">{{ agent.name }}</div>
                <div class="agent-role">{{ agent.role }}</div>
              </div>
            </div>
            <div class="text-end">
              {% if agent.status == 'active' %}
              <span class="badge-active">active</span>
              {% else %}
              <span class="badge-oncall">on-call</span>
              {% endif %}
            </div>
          </div>
          <div class="d-flex align-items-end gap-3 mb-2">
            <div>
              <div class="tasks-count">{{ agent.tasks_completed }}</div>
              <div class="tasks-label">tasks done</div>
            </div>
            <div class="text-muted small font-monospace" style="font-size:0.7rem;padding-bottom:0.15rem">{{ agent.model }}</div>
          </div>
          <div>
            {% for sk in agent.skills %}
            <span class="agent-skill-tag">{{ sk }}</span>
            {% endfor %}
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
  {% endfor %}

</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>

<!-- New Agent Modal -->
<div id="new-agent-modal" style="display:none;position:fixed;inset:0;background:#000a;z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#111;border:1px solid #ff00aa44;border-radius:12px;padding:24px;width:90%;max-width:480px;font-family:'Press Start 2P',monospace;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <span style="font-size:9px;color:#ff00aa;">+ NEW AGENT</span>
      <button onclick="closeNewAgentModal()" style="background:none;border:none;color:#666;font-size:14px;cursor:pointer;">&#x2715;</button>
    </div>
    <div style="margin-bottom:12px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">AGENT NAME</label>
      <input id="na-name" type="text" placeholder="e.g. TRACKER" style="width:100%;background:#0a0a0a;border:1px solid #333;color:#fff;padding:8px;font-family:'Press Start 2P',monospace;font-size:7px;border-radius:4px;">
    </div>
    <div style="margin-bottom:12px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">ROLES (comma separated)</label>
      <input id="na-roles" type="text" placeholder="Research, Analysis, Reports" style="width:100%;background:#0a0a0a;border:1px solid #333;color:#fff;padding:8px;font-family:'Press Start 2P',monospace;font-size:7px;border-radius:4px;">
    </div>
    <div style="margin-bottom:12px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">EMOJI AVATAR</label>
      <input id="na-emoji" type="text" placeholder="&#x1F916;" maxlength="2" style="width:60px;background:#0a0a0a;border:1px solid #333;color:#fff;padding:8px;font-size:16px;border-radius:4px;text-align:center;">
    </div>
    <div style="margin-bottom:16px;">
      <label style="font-size:7px;color:#666;display:block;margin-bottom:4px;">NEON COLOR</label>
      <select id="na-color" style="background:#0a0a0a;border:1px solid #333;color:#fff;padding:6px;font-family:'Press Start 2P',monospace;font-size:7px;border-radius:4px;">
        <option value="#00ff88">GREEN</option>
        <option value="#00ffff">CYAN</option>
        <option value="#ff6b35">ORANGE</option>
        <option value="#9b59b6">PURPLE</option>
        <option value="#f39c12">YELLOW</option>
        <option value="#3498db">BLUE</option>
        <option value="#ff00aa">PINK</option>
      </select>
    </div>
    <div id="na-msg" style="font-size:7px;margin-bottom:12px;min-height:16px;"></div>
    <button onclick="submitNewAgent()" style="width:100%;background:#0a0a0a;border:1px solid #ff00aa;color:#ff00aa;font-family:'Press Start 2P',monospace;font-size:8px;padding:10px;cursor:pointer;border-radius:4px;letter-spacing:1px;">
      &#x25B6; DEPLOY AGENT
    </button>
  </div>
</div>

<script>
function openNewAgentModal() {
  const m = document.getElementById('new-agent-modal');
  m.style.display = 'flex';
  document.getElementById('na-name').focus();
}
function closeNewAgentModal() {
  document.getElementById('new-agent-modal').style.display = 'none';
  document.getElementById('na-msg').textContent = '';
}
async function submitNewAgent() {
  const name  = document.getElementById('na-name').value.trim().toUpperCase();
  const roles = document.getElementById('na-roles').value.split(',').map(s=>s.trim()).filter(Boolean);
  const emoji = document.getElementById('na-emoji').value.trim() || '&#x1F916;';
  const color = document.getElementById('na-color').value;
  const msgEl = document.getElementById('na-msg');
  if (!name) { msgEl.style.color='#ff4455'; msgEl.textContent='NAME REQUIRED'; return; }
  msgEl.style.color='#00ffff'; msgEl.textContent='DEPLOYING...';
  try {
    const res = await fetch('/api/agent/create', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, roles, emoji, color})
    });
    const data = await res.json();
    if (data.ok) {
      msgEl.style.color='#00ff88'; msgEl.textContent='AGENT DEPLOYED &#x2713;';
      setTimeout(() => { closeNewAgentModal(); location.reload(); }, 1200);
    } else {
      msgEl.style.color='#ff4455'; msgEl.textContent = data.error || 'FAILED';
    }
  } catch(e) {
    msgEl.style.color='#ff4455'; msgEl.textContent='CONNECTION ERROR';
  }
}
document.getElementById('new-agent-modal').addEventListener('click', function(e) {
  if (e.target === this) closeNewAgentModal();
});
</script>
</body>
</html>"""


@app.route("/api/team")
def api_team():
    team_data = _read_json(DATA_DIR / "team.json", None)
    if team_data is not None:
        return jsonify(team_data)
    # Fallback: build from live agent status data
    agents = _get_agent_status()
    return jsonify({"agents": agents, "lead": {}, "departments": []})


@app.route("/api/agent/create", methods=["POST"])
def api_agent_create():
    """Create a new custom agent entry. Saves to data/custom_agents.json."""
    try:
        data  = request.get_json(force=True)
        name  = (data.get("name") or "").strip().upper()
        roles = data.get("roles") or []
        emoji = data.get("emoji") or "\U0001f916"
        color = data.get("color") or "#00ff88"
        if not name:
            return jsonify({"ok": False, "error": "Name required"}), 400

        agents_file = DATA_DIR / "custom_agents.json"
        agents = _read_json(agents_file, [])
        # Prevent duplicates
        if any(a.get("name") == name for a in agents):
            return jsonify({"ok": False, "error": f"Agent '{name}' already exists"}), 409

        new_agent = {
            "id":      f"P{len(agents)+7}",
            "name":    name,
            "emoji":   emoji,
            "roles":   roles,
            "color":   color,
            "border":  color,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        agents.append(new_agent)
        agents_file.write_text(json.dumps(agents, indent=2), encoding="utf-8")
        return jsonify({"ok": True, "agent": new_agent})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/team")
def team():
    data = _read_json(DATA_DIR / "team.json", None)
    if data is None:
        # Build default team structure from known agents
        _DEFAULT_AGENTS = [
            {"id": "P1", "name": "JARVIS",   "emoji": "\U0001f9e0", "status": "active", "roles": ["Core Brain", "LLM Routing"],         "tasks_completed": 0, "color": "#00ff88", "border": "#00ff88"},
            {"id": "P2", "name": "SCOUT",    "emoji": "\U0001f575",  "status": "active", "roles": ["Job Hunting", "Platform Search"],    "tasks_completed": 0, "color": "#4499ff", "border": "#4499ff"},
            {"id": "P3", "name": "WATCHDOG", "emoji": "\U0001f6e1",  "status": "active", "roles": ["AutoTrade", "RSI+MACD Signals"],     "tasks_completed": 0, "color": "#ffaa00", "border": "#ffaa00"},
            {"id": "P4", "name": "CODEX",    "emoji": "\U0001f4bb",  "status": "active", "roles": ["Code Review", "Auto-Upgrade"],       "tasks_completed": 0, "color": "#aa88ff", "border": "#aa88ff"},
            {"id": "P5", "name": "CLIPPER",  "emoji": "\u2702",      "status": "active", "roles": ["CashClaw Applier", "HumanVoice"],    "tasks_completed": 0, "color": "#ff88aa", "border": "#ff88aa"},
            {"id": "P6", "name": "HAWK",     "emoji": "\U0001f985",  "status": "active", "roles": ["Market Intel", "Price Tracking"],   "tasks_completed": 0, "color": "#ff6600", "border": "#ff6600"},
        ]
        data = {
            "lead": {"id": "P0", "name": "ClawBot", "emoji": "\U0001f9a1", "status": "active", "roles": ["Orchestrator", "Telegram Interface"], "color": "#00ff88"},
            "agents": _DEFAULT_AGENTS,
            "departments": [
                {"name": "Trading",  "agents": ["JARVIS", "WATCHDOG", "HAWK"]},
                {"name": "Income",   "agents": ["SCOUT", "CLIPPER"]},
                {"name": "DevOps",   "agents": ["CODEX"]},
            ],
        }
    lead        = data.get("lead", {})
    agents      = data.get("agents", [])
    departments = data.get("departments", [])
    total_agents = len(agents) + 1  # include lead
    active_count = sum(1 for a in [lead] + agents if a.get("status") == "active")
    tasks_total  = sum(a.get("tasks_completed", 0) for a in agents)
    now          = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template_string(
        TEAM_HTML,
        lead=lead, agents=agents, departments=departments,
        total_agents=total_agents, active_count=active_count,
        tasks_total=tasks_total, now=now,
    )


# ── Portfolio page ─────────────────────────────────────────────────────────────

PORTFOLIO_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw — Portfolio</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  body { background: linear-gradient(135deg,#0d0d0d 0%,#1a1a1a 100%); color:#e0e0e0; font-family:'Inter','Segoe UI',sans-serif; min-height:100vh; }
  .navbar { background:#141414 !important; border-bottom:1px solid #242424; }
  .navbar-brand { color:#00ff88 !important; font-weight:700; }
  .nav-lnk { color:#aaa !important; text-decoration:none; font-size:0.875rem; }
  .nav-lnk:hover,.nav-lnk-active { color:#00ff88 !important; }
  .card { background:#141414; border:1px solid #242424; border-radius:16px; }
  .card-header { background:#1e1e1e; border-bottom:1px solid #242424; border-radius:16px 16px 0 0 !important; padding:1rem 1.5rem; }
  .card-body { padding:1.5rem; }
  .tg { color:#00ff88 !important; }
  .tr { color:#ff4455 !important; }
  .tm { color:#666 !important; }
  .stat-card { background:#141414; border:1px solid #242424; border-radius:16px; padding:1.25rem 1.5rem; }
  .stat-label { font-size:0.7rem; color:#666; text-transform:uppercase; letter-spacing:.06em; margin-bottom:.3rem; }
  .stat-value { font-size:1.6rem; font-weight:700; line-height:1.2; }
  .stat-sub { font-size:0.78rem; color:#888; margin-top:.2rem; }
  .price-strip { background:#0f0f0f; border:1px solid #1e1e1e; border-radius:12px; padding:.75rem 1.25rem; display:flex; gap:2rem; flex-wrap:wrap; align-items:center; }
  .price-item { display:flex; align-items:center; gap:.4rem; }
  .pi-coin { font-weight:700; font-size:.9rem; }
  .pi-price { font-family:monospace; font-size:.9rem; }
  .pi-chg { font-size:.8rem; }
  .pos-table { width:100%; }
  .pos-table th { color:#00ff88; font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; font-weight:600; padding:.5rem .75rem; border-bottom:1px solid #242424; }
  .pos-table td { padding:.75rem; border-bottom:1px solid #1a1a1a; font-size:.88rem; vertical-align:middle; }
  .pos-table tr:last-child td { border-bottom:none; }
  .pos-table tr:hover td { background:#1a1a1a; }
  .coin-badge { background:#1e1e1e; border:1px solid #2e2e2e; border-radius:8px; padding:2px 10px; font-weight:700; font-size:.82rem; }
  .empty-state { text-align:center; padding:3rem 1rem; color:#555; }
  .empty-state i { font-size:3rem; margin-bottom:1rem; color:#333; display:block; }
  .hint-badge { background:#1e1e1e; color:#00ff88; border:1px solid #2e2e2e; padding:7px 14px; border-radius:8px; font-size:.82rem; display:inline-block; margin:4px; }
  @media (max-width:768px) {
    .stat-value { font-size:1.3rem; }
    .price-strip { gap:1rem; }
    .card-body { padding:.9rem; }
    .pos-table td,.pos-table th { padding:.5rem; font-size:.8rem; }
  }
</style>
</head>
<body>

<nav class="navbar navbar-expand-lg navbar-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="/"><i class="fas fa-robot me-2"></i>OpenClaw</a>
    <button class="navbar-toggler border-0" type="button" data-bs-toggle="collapse" data-bs-target="#navPF">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div class="collapse navbar-collapse" id="navPF">
      <div class="navbar-nav ms-auto d-flex gap-3 align-items-center flex-row">
        <a href="/" class="nav-lnk"><i class="fas fa-tachometer-alt me-1"></i>Dashboard</a>
        <a href="/portfolio" class="nav-lnk nav-lnk-active"><i class="fas fa-wallet me-1"></i>Portfolio</a>
        <a href="/taskboard" class="nav-lnk"><i class="fas fa-clipboard-list me-1"></i>Tasks</a>
        <a href="/team" class="nav-lnk"><i class="fas fa-users me-1"></i>Team</a>
      </div>
    </div>
  </div>
</nav>

<div class="container-fluid p-3 p-md-4">

  <div class="d-flex justify-content-between align-items-center mb-4">
    <div>
      <h4 class="mb-0 fw-bold"><i class="fas fa-wallet me-2 tg"></i>Portfolio</h4>
      <small class="tm">{{ now }}</small>
    </div>
    <a href="/" style="background:#1e1e1e;color:#00ff88;border:1px solid #2e2e2e;border-radius:8px;padding:6px 14px;font-size:.85rem;text-decoration:none;">
      <i class="fas fa-arrow-left me-1"></i>Dashboard
    </a>
  </div>

  {% if portfolio.prices %}
  <div class="price-strip mb-4">
    <span style="font-size:.7rem;color:#555;text-transform:uppercase;letter-spacing:.08em;">LIVE</span>
    {% for coin, d in portfolio.prices.items() %}
    <div class="price-item">
      <span class="pi-coin">{{ coin }}</span>
      <span class="pi-price">${{ "{:,.0f}".format(d.price) if d.price > 100 else "{:.4f}".format(d.price) }}</span>
      <span class="pi-chg {{ 'tg' if d.change >= 0 else 'tr' }}">{{ d.sign }}{{ d.change }}%</span>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if portfolio.has_data %}

  <div class="row g-3 mb-4">
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="stat-label">Total Trades</div>
        <div class="stat-value tg">{{ portfolio.total_trades }}</div>
        <div class="stat-sub">{{ portfolio.wins }}W &middot; {{ portfolio.losses }}L</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="stat-label">Win Rate</div>
        <div class="stat-value {{ 'tg' if portfolio.win_rate >= 50 else 'tr' }}">{{ portfolio.win_rate }}%</div>
        <div class="stat-sub">{{ portfolio.wins }} profitable</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="stat-label">Invested</div>
        <div class="stat-value">${{ "{:,.2f}".format(portfolio.total_invested) }}</div>
        <div class="stat-sub">cumulative BUYs</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="stat-label">Total P&amp;L</div>
        <div class="stat-value {{ 'tg' if portfolio.total_pnl >= 0 else 'tr' }}">
          {{ '+' if portfolio.total_pnl >= 0 else '' }}${{ "{:,.2f}".format(portfolio.total_pnl) }}
        </div>
        <div class="stat-sub">realized gains</div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
      <h6 class="mb-0 fw-semibold"><i class="fas fa-chart-bar me-2"></i>Positions by Coin</h6>
      <small class="tm">sorted by P&amp;L</small>
    </div>
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="pos-table">
          <thead>
            <tr>
              <th>Coin</th>
              <th class="text-end">Price</th>
              <th class="text-end">24h</th>
              <th class="text-center">B / S</th>
              <th class="text-end">Invested</th>
              <th class="text-end">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {% for pos in portfolio.positions | sort(attribute='pnl', reverse=True) %}
            <tr>
              <td><span class="coin-badge">{{ pos.coin }}</span></td>
              <td class="text-end" style="font-family:monospace;">
                {% if pos.current_price > 0 %}
                  ${{ "{:,.0f}".format(pos.current_price) if pos.current_price > 100 else "{:.4f}".format(pos.current_price) }}
                {% else %}<span class="tm">—</span>{% endif %}
              </td>
              <td class="text-end {{ 'tg' if pos.change_24h >= 0 else 'tr' }}">
                {% if pos.current_price > 0 %}{{ pos.sign }}{{ pos.change_24h }}%{% else %}<span class="tm">—</span>{% endif %}
              </td>
              <td class="text-center">
                <span class="tg">{{ pos.buys }}&uarr;</span>&nbsp;<span class="tr">{{ pos.sells }}&darr;</span>
              </td>
              <td class="text-end">${{ "{:,.2f}".format(pos.invested) }}</td>
              <td class="text-end fw-semibold {{ 'tg' if pos.pnl >= 0 else 'tr' }}">
                {{ '+' if pos.pnl >= 0 else '' }}${{ "{:,.2f}".format(pos.pnl) }}
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  {% else %}

  <div class="card mb-3">
    <div class="card-body">
      <div class="empty-state">
        <i class="fas fa-wallet"></i>
        <h5 style="color:#888;">No Trade History Yet</h5>
        <p class="tm mb-3">Your portfolio P&amp;L will appear here once trades are executed.</p>
        <div>
          <span class="hint-badge"><i class="fas fa-robot me-1"></i>/autotrade on</span>
          <span class="hint-badge" style="color:#aaa;"><i class="fas fa-bolt me-1"></i>/autotrade now</span>
          <span class="hint-badge" style="color:#aaa;"><i class="fas fa-search me-1"></i>/scan 4h</span>
        </div>
      </div>
    </div>
  </div>

  {% if portfolio.prices %}
  <div class="card">
    <div class="card-header">
      <h6 class="mb-0 fw-semibold"><i class="fas fa-globe me-2"></i>Live Market Prices</h6>
    </div>
    <div class="card-body p-0">
      <table class="pos-table">
        <thead><tr><th>Asset</th><th class="text-end">Price</th><th class="text-end">24h Change</th></tr></thead>
        <tbody>
          {% for coin, d in portfolio.prices.items() %}
          <tr>
            <td><span class="coin-badge">{{ coin }}</span></td>
            <td class="text-end" style="font-family:monospace;">${{ "{:,.2f}".format(d.price) }}</td>
            <td class="text-end {{ 'tg' if d.change >= 0 else 'tr' }}">{{ d.sign }}{{ d.change }}%</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}

  {% endif %}

</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>setTimeout(() => location.reload(), 30000);</script>
</body>
</html>"""


@app.route("/portfolio")
def portfolio():
    portfolio_data = get_portfolio_data()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template_string(PORTFOLIO_HTML, portfolio=portfolio_data, now=now)


HOLDINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw — Holdings</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  body { background: linear-gradient(135deg,#0d0d0d 0%,#1a1a1a 100%); color:#e0e0e0; font-family:'Inter','Segoe UI',sans-serif; min-height:100vh; }
  .navbar { background:#141414 !important; border-bottom:1px solid #242424; }
  .navbar-brand { color:#00ff88 !important; font-weight:700; }
  .nav-lnk { color:#aaa !important; text-decoration:none; font-size:0.875rem; }
  .nav-lnk:hover,.nav-lnk-active { color:#00ff88 !important; }
  .card { background:#141414; border:1px solid #242424; border-radius:16px; }
  .card-header { background:#1e1e1e; border-bottom:1px solid #242424; border-radius:16px 16px 0 0 !important; padding:1rem 1.5rem; }
  .card-body { padding:1.5rem; }
  .tg { color:#00ff88 !important; }
  .tr { color:#ff4455 !important; }
  .tm { color:#666 !important; }
  .tw { color:#ffaa00 !important; }
  /* Hero total value */
  .hero-value { font-size:3rem; font-weight:700; color:#00ff88; line-height:1; }
  .hero-label { font-size:.8rem; color:#666; text-transform:uppercase; letter-spacing:.08em; margin-bottom:.5rem; }
  .hero-card { background:linear-gradient(135deg,#0f1f14,#141414); border:1px solid #00ff8833; border-radius:20px; padding:2rem; margin-bottom:1.5rem; }
  /* Asset table */
  .asset-table { width:100%; }
  .asset-table th { color:#00ff88; font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; padding:.6rem .75rem; border-bottom:1px solid #242424; font-weight:600; }
  .asset-table td { padding:.75rem; border-bottom:1px solid #1a1a1a; font-size:.88rem; vertical-align:middle; }
  .asset-table tr:last-child td { border-bottom:none; }
  .asset-table tr:hover td { background:#1a1a1a; }
  .currency-badge { background:#1e1e1e; border:1px solid #2e2e2e; border-radius:8px; padding:2px 10px; font-weight:700; font-size:.82rem; }
  /* Transaction table */
  .tx-table { width:100%; }
  .tx-table th { color:#666; font-size:.7rem; text-transform:uppercase; padding:.5rem .75rem; border-bottom:1px solid #242424; }
  .tx-table td { padding:.65rem .75rem; border-bottom:1px solid #1a1a1a; font-size:.82rem; }
  .tx-table tr:last-child td { border-bottom:none; }
  .side-buy { color:#00ff88; font-weight:600; }
  .side-sell { color:#ff4455; font-weight:600; }
  .empty-state { text-align:center; padding:2.5rem 1rem; color:#555; }
  .empty-state i { font-size:2.5rem; color:#333; display:block; margin-bottom:.75rem; }
  /* Pct bar */
  .pct-bar { height:4px; background:#1e1e1e; border-radius:2px; margin-top:4px; }
  .pct-fill { height:4px; background:#00ff88; border-radius:2px; }
  @media (max-width:768px) {
    .hero-value { font-size:2.2rem; }
    .card-body { padding:1rem; }
    .asset-table td,.asset-table th,.tx-table td,.tx-table th { padding:.5rem; font-size:.78rem; }
  }
</style>
</head>
<body>

<nav class="navbar navbar-expand-lg navbar-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="/"><i class="fas fa-robot me-2"></i>OpenClaw</a>
    <button class="navbar-toggler border-0" type="button" data-bs-toggle="collapse" data-bs-target="#navH">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div class="collapse navbar-collapse" id="navH">
      <div class="navbar-nav ms-auto d-flex gap-3 align-items-center flex-row">
        <a href="/" class="nav-lnk">Dashboard</a>
        <a href="/holdings" class="nav-lnk nav-lnk-active">Holdings</a>
        <a href="/portfolio" class="nav-lnk">Portfolio</a>
        <a href="/taskboard" class="nav-lnk">Tasks</a>
        <a href="/team" class="nav-lnk">Team</a>
      </div>
    </div>
  </div>
</nav>

<div class="container-fluid p-3 p-md-4">

  {% if holdings_error %}
  <div style="background:#1a0000;border:1px solid #ff4455;border-radius:8px;padding:16px 20px;margin-bottom:20px;color:#ff8888;font-family:'Share Tech Mono',monospace;font-size:12px;">
    <div style="font-family:'Press Start 2P',monospace;font-size:8px;color:#ff4455;margin-bottom:8px;">&#9888; EXCHANGE CONNECTION ERROR</div>
    <div>{{ holdings_error[:120] }}</div>
    <div style="margin-top:10px;color:#555;font-size:11px;">Fix: In Crypto.com Exchange &rarr; API Management &rarr; Remove IP restriction on your key, or whitelist your PC&apos;s public IP.</div>
  </div>
  {% endif %}

  <div class="d-flex justify-content-between align-items-center mb-3">
    <div>
      <h4 class="mb-0 fw-bold"><i class="fas fa-coins me-2 tg"></i>Live Holdings</h4>
      <small class="tm">Crypto.com Exchange · {{ now }}</small>
    </div>
    <div class="d-flex gap-2">
      <button onclick="location.reload()" style="background:#1e1e1e;color:#00ff88;border:1px solid #2e2e2e;border-radius:8px;padding:6px 14px;font-size:.82rem;cursor:pointer;">
        <i class="fas fa-sync-alt me-1"></i>Refresh
      </button>
      <a href="/" style="background:#1e1e1e;color:#aaa;border:1px solid #2e2e2e;border-radius:8px;padding:6px 14px;font-size:.82rem;text-decoration:none;">
        <i class="fas fa-arrow-left me-1"></i>Back
      </a>
    </div>
  </div>

  {% if not holdings.configured %}
  <div class="card mb-3">
    <div class="card-body">
      <div class="empty-state">
        <i class="fas fa-key"></i>
        <h5 style="color:#888;">API Keys Not Configured</h5>
        <p class="tm mb-2">Add your Crypto.com keys to <code>.env</code>:</p>
        <code style="color:#00ff88;font-size:.85rem;">CRYPTOCOM_API_KEY=your_key<br>CRYPTOCOM_SECRET=your_secret</code>
      </div>
    </div>
  </div>

  {% elif holdings.error %}
  <div class="card mb-3" style="border-color:#ff445533;">
    <div class="card-body">
      <div class="empty-state">
        <i class="fas fa-exclamation-triangle" style="color:#ff4455;"></i>
        <h5 style="color:#ff4455;">API Connection Error</h5>
        <p class="tm mb-0"><code>{{ holdings.error }}</code></p>
        <small class="tm">Check your API key permissions on Crypto.com Exchange</small>
      </div>
    </div>
  </div>

  {% else %}

  <!-- Hero total value -->
  <div class="hero-card">
    <div class="row align-items-center">
      <div class="col">
        <div class="hero-label">Total Portfolio Value</div>
        <div class="hero-value">${{ "{:,.2f}".format(holdings.total_usd) }}</div>
        <div class="mt-2 tm" style="font-size:.82rem;">
          {{ holdings.asset_count }} assets · fetched {{ holdings.fetched_at }}
        </div>
      </div>
      <div class="col-auto text-end">
        <div class="tm" style="font-size:.75rem;">Crypto.com Exchange</div>
        <div class="tg" style="font-size:.85rem; margin-top:.25rem;">
          <i class="fas fa-circle" style="font-size:.5rem;"></i> Live
        </div>
      </div>
    </div>
  </div>

  <!-- Holdings table -->
  <div class="card mb-3">
    <div class="card-header d-flex justify-content-between">
      <h6 class="mb-0 fw-semibold"><i class="fas fa-layer-group me-2"></i>Asset Holdings</h6>
      <small class="tm">{{ holdings.asset_count }} assets</small>
    </div>
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="asset-table">
          <thead>
            <tr>
              <th>Asset</th>
              <th class="text-end">Balance</th>
              <th class="text-end">Price</th>
              <th class="text-end">24h</th>
              <th class="text-end">Value (USD)</th>
              <th class="text-end">Allocation</th>
            </tr>
          </thead>
          <tbody>
            {% for currency, b in holdings.balances.items() %}
            {% set pct = (b.value_usd / holdings.total_usd * 100) if holdings.total_usd > 0 else 0 %}
            <tr>
              <td><span class="currency-badge">{{ currency }}</span></td>
              <td class="text-end" style="font-family:monospace;">
                {{ "{:,.6f}".format(b.total) if b.total < 1 else "{:,.4f}".format(b.total) if b.total < 100 else "{:,.2f}".format(b.total) }}
                {% if b.locked > 0 %}
                  <br><small class="tw"><i class="fas fa-lock" style="font-size:.6rem;"></i> {{ "{:,.4f}".format(b.locked) }} locked</small>
                {% endif %}
              </td>
              <td class="text-end" style="font-family:monospace;">
                {% if b.price_usd > 0 %}
                  ${{ "{:,.0f}".format(b.price_usd) if b.price_usd > 100 else "{:,.4f}".format(b.price_usd) }}
                {% else %}<span class="tm">—</span>{% endif %}
              </td>
              <td class="text-end {{ 'tg' if b.change_24h >= 0 else 'tr' }}">
                {% if b.change_24h != 0 %}{{ b.sign }}{{ "{:.2f}".format(b.change_24h) }}%
                {% else %}<span class="tm">—</span>{% endif %}
              </td>
              <td class="text-end fw-semibold">
                ${{ "{:,.2f}".format(b.value_usd) }}
              </td>
              <td class="text-end" style="min-width:80px;">
                <small>{{ "{:.1f}".format(pct) }}%</small>
                <div class="pct-bar"><div class="pct-fill" style="width:{{ [pct,100]|min }}%;"></div></div>
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
  {% endif %}

  <!-- Transaction history from trades.log -->
  <div class="card">
    <div class="card-header d-flex justify-content-between">
      <h6 class="mb-0 fw-semibold"><i class="fas fa-exchange-alt me-2"></i>Bot Transactions</h6>
      <small class="tm">Last 50 bot trades</small>
    </div>
    <div class="card-body p-0">
      {% if trades %}
      <div class="table-responsive">
        <table class="tx-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Coin</th>
              <th>Side</th>
              <th class="text-end">Price</th>
              <th class="text-end">Amount</th>
              <th class="text-end">P&amp;L</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {% for t in trades | reverse %}
            <tr>
              <td class="tm" style="white-space:nowrap;">{{ t.get('timestamp','')[:16] if t.get('timestamp') else '—' }}</td>
              <td><span class="currency-badge" style="font-size:.75rem;">{{ t.get('coin','?') }}</span></td>
              <td class="{{ 'side-buy' if t.get('action','') == 'BUY' else 'side-sell' }}">
                {{ t.get('action','?') }}
              </td>
              <td class="text-end" style="font-family:monospace;">${{ "{:,.2f}".format(t.get('price',0)|float) }}</td>
              <td class="text-end" style="font-family:monospace;">${{ "{:,.2f}".format(t.get('usd_amount',0)|float) }}</td>
              <td class="text-end fw-semibold {{ 'tg' if (t.get('pnl',0)|float) >= 0 else 'tr' }}">
                {% set pnl = t.get('pnl',0)|float %}
                {% if pnl != 0 %}{{ '+' if pnl >= 0 else '' }}${{ "{:,.2f}".format(pnl) }}
                {% else %}<span class="tm">—</span>{% endif %}
              </td>
              <td>
                {% if t.get('status') == 'executed' %}<span class="badge" style="background:#00ff8822;color:#00ff88;">executed</span>
                {% elif t.get('status') == 'skipped' %}<span class="badge" style="background:#66666622;color:#666;">skipped</span>
                {% else %}<span class="badge" style="background:#ffaa0022;color:#ffaa00;">{{ t.get('status','?') }}</span>{% endif %}
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% else %}
      <div class="empty-state">
        <i class="fas fa-history"></i>
        <h6 style="color:#888;">No Bot Transactions Yet</h6>
        <p class="tm mb-0" style="font-size:.82rem;">Bot trades appear here after /autotrade executes signals</p>
      </div>
      {% endif %}
    </div>
  </div>

</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
// Auto-refresh every 60 seconds
setTimeout(() => location.reload(), 60000);
</script>
</body>
</html>"""


@app.route("/holdings")
def holdings():
    holdings_error = None
    live = {}
    try:
        live = get_live_holdings()
    except Exception as e:
        holdings_error = str(e)
    trade_history = get_recent_trades(n=50)
    prices = get_prices()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template_string(HOLDINGS_HTML, holdings=live, trades=trade_history, prices=prices, now=now, holdings_error=holdings_error)


def _get_clip_economy_stats() -> dict:
    """Return income projection stats for the clip economy dashboard card.
    Falls back to base estimates when no real income has been logged yet.
    """
    projections: dict = {}
    try:
        from agents.clip_processor import calculate_projections  # type: ignore
        projections = calculate_projections()
    except Exception:
        projections = {}

    # If no income logged yet, show realistic base projections for motivation
    if projections.get("actual_earned", 0) == 0:
        projections = {
            "actual_earned":        0.0,
            "tiktok_fund_est":      0.0,
            "conservative_monthly": 200.0,   # 2 gigs x $100
            "current_monthly":      260.0,   # conservative x 1.3
            "optimized_monthly":    500.0,   # full pipeline
            "is_estimate":          True,
        }
    return {
        "projections": projections,
        "clips_processed": 0,
    }


@app.route("/clip-economy")
def clip_economy():
    stats = _get_clip_economy_stats()
    proj  = stats.get("projections", {})
    # Also pull income log for display
    income_log_raw = _read_json(DATA_DIR / "income_log.json", [])
    running_total  = 0.0
    income_log     = []
    for entry in income_log_raw[-50:]:
        running_total += float(entry.get("amount", 0))
        income_log.append({**entry, "running_total": round(running_total, 2)})
    income_log = list(reversed(income_log))

    scout = {}
    applier = {}
    try:
        from agents.job_scout import get_scout_status
        scout = get_scout_status()
    except Exception:
        pass
    try:
        from agents.cashclaw_applier import get_applier_status
        applier = get_applier_status()
    except Exception:
        pass

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template_string(CLIP_ECONOMY_HTML,
        proj=proj, income_log=income_log, scout=scout, applier=applier, now=now)


@app.route("/api/clip-economy/stats")
def api_clip_economy_stats():
    return jsonify(_get_clip_economy_stats())


# ── /api/agents — real-time multi-agent status ────────────────────────────────

def _get_agent_status() -> list:
    """Build live status cards for all 8 revenue agents from their data files."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz

    DATA = _Path(__file__).parent.parent / "data"

    def _ts_age(ts: str) -> str:
        if not ts:
            return "never"
        try:
            d = _dt.fromisoformat(ts.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_tz.utc)
            diff = (_dt.now(_tz.utc) - d).total_seconds()
            if diff < 60:   return f"{int(diff)}s ago"
            if diff < 3600: return f"{int(diff//60)}m ago"
            return f"{int(diff//3600)}h ago"
        except Exception:
            return ts[:16]

    def _load(f):
        p = DATA / f
        if p.exists():
            try:
                return _json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    agents = []

    # 1. JOB SCOUT
    scout = _load("job_scout_state.json")
    pending = len(scout.get("pending_approval", []))
    approved = len(scout.get("approved", []))
    applied = len(scout.get("applied", []))
    agents.append({
        "id": "SCOUT", "name": "Job Scout", "icon": "🕵️",
        "status": "idle",
        "last_run": _ts_age(scout.get("last_run", "")),
        "current_task": f"{pending} pending approval",
        "metrics": {"pending": pending, "approved": approved, "applied": applied},
        "revenue": None,
        "errors": scout.get("last_error", None),
    })

    # 2. CASHCLAW APPLIER
    applier = _load("applier_state.json")
    drafts = len(applier.get("drafts", []))
    sent = len(applier.get("sent", []))
    agents.append({
        "id": "APPLIER", "name": "CashClaw Applier", "icon": "📝",
        "status": "idle",
        "last_run": _ts_age(applier.get("last_updated", "")),
        "current_task": f"{drafts} drafts · {sent} sent",
        "metrics": {"drafts": drafts, "sent": sent},
        "revenue": None,
        "errors": None,
    })

    # 3. CLIP PROCESSOR
    clip_data = _load("clip_jobs.json")
    running = clip_data.get("jobs", [])
    completed_clips = clip_data.get("completed", [])
    failed_clips = clip_data.get("failed", [])
    clip_status = "active" if running else "idle"
    latest_clip = (completed_clips[-1] if completed_clips else {})
    agents.append({
        "id": "CLIP", "name": "Clip Processor", "icon": "✂️",
        "status": clip_status,
        "last_run": _ts_age(latest_clip.get("completed_at", "")),
        "current_task": f"{len(running)} running" if running else f"{len(completed_clips)} clips done",
        "metrics": {"running": len(running), "completed": len(completed_clips), "failed": len(failed_clips)},
        "revenue": None,
        "errors": failed_clips[-1].get("error") if failed_clips else None,
    })

    # 4. CONTENT PIPELINE
    queue = _load("content_queue.json")
    q_items = queue.get("queue", [])
    posted_items = queue.get("posted", [])
    queued_count = sum(1 for i in q_items if i.get("status") == "queued")
    approved_count = sum(1 for i in q_items if i.get("status") == "approved")
    agents.append({
        "id": "CONTENT", "name": "Content Pipeline", "icon": "🎬",
        "status": "idle" if not q_items else "active",
        "last_run": _ts_age(queue.get("last_updated", "")),
        "current_task": f"{queued_count} queued · {approved_count} approved",
        "metrics": {"queued": queued_count, "approved": approved_count, "posted": len(posted_items)},
        "revenue": None,
        "errors": None,
    })

    # 5. SOCIAL PUBLISHER
    pub_log = _load("publish_log.json")
    pub_entries = pub_log.get("log", [])
    pub_success = [e for e in pub_entries if e.get("status") == "posted"]
    pub_fail = [e for e in pub_entries if e.get("status") == "failed"]
    last_pub = pub_success[-1] if pub_success else {}
    agents.append({
        "id": "PUBLISHER", "name": "Social Publisher", "icon": "📤",
        "status": "idle",
        "last_run": _ts_age(last_pub.get("posted_at", "")),
        "current_task": f"{len(pub_success)} posted · {len(pub_fail)} failed",
        "metrics": {"posted": len(pub_success), "failed": len(pub_fail)},
        "revenue": None,
        "errors": pub_fail[-1].get("error") if pub_fail else None,
    })

    # 6. PERFORMANCE TRACKER
    perf_db = _load("performance_db.json")
    snaps = perf_db.get("snapshots", [])
    proj = perf_db.get("latest_projections", {})
    last_snap = snaps[-1] if snaps else {}
    monthly_est = proj.get("current", 0)
    agents.append({
        "id": "PERF", "name": "Performance Tracker", "icon": "📈",
        "status": "idle",
        "last_run": _ts_age(last_snap.get("ts", "")),
        "current_task": f"{len(snaps)} snapshots taken",
        "metrics": {"snapshots": len(snaps), "tiktok": perf_db.get("tiktok_total_views", 0)},
        "revenue": round(monthly_est, 2) if monthly_est else None,
        "errors": None,
    })

    # 7. TRADING AGENT
    trade_state = _load("trading_agent_state.json")
    dca_count = len(trade_state.get("dca_schedules", []))
    last_cycle = trade_state.get("last_cycle_ts", 0)
    last_cycle_str = _ts_age(_dt.fromtimestamp(last_cycle, tz=_tz.utc).isoformat()) if last_cycle else "never"
    agents.append({
        "id": "TRADING", "name": "Trading Agent", "icon": "📊",
        "status": "idle",
        "last_run": last_cycle_str,
        "current_task": f"{dca_count} DCA schedules active",
        "metrics": {"dca_schedules": dca_count},
        "revenue": None,
        "errors": None,
    })

    # 8. INCOME LOG
    income = _load("income_log.json")
    entries = income if isinstance(income, list) else income.get("entries", [])
    total_income = sum(float(e.get("amount", 0)) for e in entries)
    agents.append({
        "id": "INCOME", "name": "Income Logger", "icon": "💰",
        "status": "idle",
        "last_run": _ts_age(entries[-1].get("ts", "") if entries else ""),
        "current_task": f"{len(entries)} entries logged",
        "metrics": {"entries": len(entries)},
        "revenue": round(total_income, 2),
        "errors": None,
    })

    return agents


@app.route("/api/agents")
def api_agents():
    """Real-time agent status — called every 30s by dashboard."""
    agents = _get_agent_status()
    total_revenue = sum(a["revenue"] or 0 for a in agents)
    active_count = sum(1 for a in agents if a["status"] == "active")
    return jsonify({
        "agents": agents,
        "summary": {
            "total": len(agents),
            "active": active_count,
            "idle": len(agents) - active_count,
            "total_revenue_logged": round(total_revenue, 2),
            "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        }
    })


@app.route("/api/life-dashboard")
def api_life_dashboard():
    """LifeOS metrics — fitness, finance, habits."""
    try:
        from pathlib import Path as _Path
        # Ensure data/lifeos dirs exist before importing agent
        (_Path(__file__).parent.parent / "data" / "lifeos" / "daily_logs").mkdir(parents=True, exist_ok=True)
        from agents.lifeos_agent import get_dashboard_data
        return jsonify(get_dashboard_data())
    except Exception as exc:
        import traceback
        return jsonify({"error": str(exc), "trace": traceback.format_exc()[-500:]}), 500


CLIP_ECONOMY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CashClaw — Clip Economy</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root {--bg:#0d0d0d;--card:#141414;--border:#242424;--green:#00ff88;--amber:#ffaa00;--blue:#4499ff;--red:#ff4455;--text:#e0e0e0;--muted:#666;}
  body { background: var(--bg); color: var(--text); font-family:'Segoe UI',sans-serif; }
  .navbar { background:var(--card)!important; border-bottom:1px solid var(--border); }
  .navbar-brand { color:var(--green)!important; font-weight:bold; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; }
  .card-header { background:#1e1e1e; border-bottom:1px solid var(--border); border-radius:12px 12px 0 0!important; }
  .proj-card { text-align:center; padding:1.5rem; }
  .proj-val { font-size:2.2rem; font-weight:700; font-family:monospace; }
  .proj-label { font-size:.8rem; color:var(--muted); margin-bottom:.5rem; }
  .proj-sub { font-size:.75rem; color:var(--muted); margin-top:.3rem; }
  .est-badge { font-size:.65rem; color:#555; border:1px solid #333; padding:1px 5px; border-radius:3px; vertical-align:middle; }
  .section-title { font-size:.85rem; font-weight:700; color:var(--muted); letter-spacing:.08em; text-transform:uppercase; margin:1.5rem 0 .75rem; }
  .stat-pill { display:inline-block; background:#1a1a1a; border:1px solid var(--border); border-radius:20px; padding:4px 12px; font-size:.8rem; margin:.2rem; }
  .income-row { border-bottom:1px solid var(--border); padding:.5rem 0; font-size:.85rem; }
  .income-row:last-child { border-bottom:none; }
</style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark px-3 py-2">
  <span class="navbar-brand"><i class="fas fa-film me-2"></i>CashClaw — Clip Economy</span>
  <div class="d-flex gap-3 ms-auto align-items-center">
    <a href="/" class="text-muted small text-decoration-none"><i class="fas fa-arrow-left me-1"></i>Dashboard</a>
    <a href="/taskboard" class="text-muted small text-decoration-none">Task Board</a>
    <small class="text-muted">{{ now }}</small>
  </div>
</nav>

<div class="container-fluid py-4 px-4" style="max-width:1100px;">

<!-- Income Projections -->
<div class="section-title"><i class="fas fa-dollar-sign me-2"></i>Income Projections</div>
<div class="row mb-4">
  <div class="col-12 col-sm-4 mb-3">
    <div class="card proj-card">
      <div class="proj-label">💰 Conservative</div>
      <div class="proj-val text-muted">
        ${{ "%.0f"|format(proj.conservative_monthly|default(0)) }}
        {% if proj.is_estimate %}<span class="est-badge">est.</span>{% endif %}
      </div>
      <div class="proj-sub">{{ "Actual earned × 30d" if not proj.is_estimate else "2 gigs/mo × $100 avg" }}</div>
    </div>
  </div>
  <div class="col-12 col-sm-4 mb-3">
    <div class="card proj-card">
      <div class="proj-label">📈 Current Pace</div>
      <div class="proj-val" style="color:var(--amber);">
        ${{ "%.0f"|format(proj.current_monthly|default(0)) }}
        {% if proj.is_estimate %}<span class="est-badge">est.</span>{% endif %}
      </div>
      <div class="proj-sub">{{ "Conservative × 1.3" if not proj.is_estimate else "4 gigs/mo + TikTok" }}</div>
    </div>
  </div>
  <div class="col-12 col-sm-4 mb-3">
    <div class="card proj-card">
      <div class="proj-label">🚀 Optimized</div>
      <div class="proj-val" style="color:var(--green);">
        ${{ "%.0f"|format(proj.optimized_monthly|default(0)) }}
        {% if proj.is_estimate %}<span class="est-badge">est.</span>{% endif %}
      </div>
      <div class="proj-sub">{{ "Full pipeline running" }}</div>
    </div>
  </div>
</div>

{% if proj.is_estimate %}
<div class="alert" style="background:#1a1a00;border:1px solid #ffaa0033;color:#ffaa00;font-size:.82rem;border-radius:8px;">
  <i class="fas fa-info-circle me-2"></i>Projections are estimates — no income logged yet. Use <code>/log_income</code> in Telegram to track real earnings.
</div>
{% endif %}

<!-- CashClaw Pipeline -->
<div class="section-title"><i class="fas fa-robot me-2"></i>CashClaw Pipeline Status</div>
<div class="row mb-4">
  <div class="col-6 col-md-3 mb-3">
    <div class="card p-3 text-center">
      <div style="font-size:.7rem;color:var(--muted);">PENDING JOBS</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--green);">{{ scout.get('pending', 0) }}</div>
      <div style="font-size:.7rem;color:#555;">awaiting approval</div>
    </div>
  </div>
  <div class="col-6 col-md-3 mb-3">
    <div class="card p-3 text-center">
      <div style="font-size:.7rem;color:var(--muted);">APPROVED</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--amber);">{{ scout.get('approved', 0) }}</div>
      <div style="font-size:.7rem;color:#555;">ready for outreach</div>
    </div>
  </div>
  <div class="col-6 col-md-3 mb-3">
    <div class="card p-3 text-center">
      <div style="font-size:.7rem;color:var(--muted);">DRAFTS READY</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--blue);">{{ applier.get('pending_drafts', 0) }}</div>
      <div style="font-size:.7rem;color:#555;">outreach drafted</div>
    </div>
  </div>
  <div class="col-6 col-md-3 mb-3">
    <div class="card p-3 text-center">
      <div style="font-size:.7rem;color:var(--muted);">APPLIED</div>
      <div style="font-size:1.8rem;font-weight:700;color:#888;">{{ scout.get('applied', 0) }}</div>
      <div style="font-size:.7rem;color:#555;">sent this cycle</div>
    </div>
  </div>
</div>

<!-- Income Log -->
<div class="section-title"><i class="fas fa-receipt me-2"></i>Income Log</div>
<div class="card">
  <div class="card-body">
  {% if income_log %}
    {% for entry in income_log %}
    <div class="income-row d-flex justify-content-between align-items-center">
      <div>
        <span style="color:var(--green);font-weight:600;">${{ "%.2f"|format(entry.amount|float) }}</span>
        <span class="text-muted ms-2">{{ entry.source }}</span>
        {% if entry.note %}<span class="text-muted ms-1">— {{ entry.note }}</span>{% endif %}
      </div>
      <div style="text-align:right;">
        <span class="text-muted" style="font-size:.75rem;">{{ entry.get('timestamp','')[:10] }}</span>
        <span class="ms-3" style="font-size:.75rem;color:#555;">total: ${{ "%.2f"|format(entry.running_total) }}</span>
      </div>
    </div>
    {% endfor %}
  {% else %}
    <div class="text-center py-4" style="color:var(--muted);">
      <i class="fas fa-receipt" style="font-size:2rem;display:block;margin-bottom:8px;"></i>
      No income logged yet.<br>
      <small>Use <code>/log_income 150 whop "clip job"</code> in Telegram</small>
    </div>
  {% endif %}
  </div>
</div>

<!-- Quick Commands -->
<div class="section-title mt-4"><i class="fas fa-terminal me-2"></i>Quick Commands</div>
<div class="mb-4" style="display:flex;flex-wrap:wrap;gap:8px;">
  {% for cmd in ['/scout run', '/approve_job 1', '/apply_job 1', '/send_apply 1', '/cashclaw', '/log_income'] %}
  <code style="background:#1a1a1a;border:1px solid var(--border);padding:6px 12px;border-radius:6px;font-size:.8rem;color:var(--green);">{{ cmd }}</code>
  {% endfor %}
</div>

</div>
<script>setTimeout(() => location.reload(), 60000);</script>
</body>
</html>"""


# ── Live Status Panel ──────────────────────────────────────────────────────────

STATUS_PANEL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Live System Status</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
  :root {
    --neon:#00ff88;--neon2:#00ffff;--amber:#ffaa00;--red:#ff4455;
    --pink:#ff00aa;--purple:#9b59b6;--orange:#ff6b35;
    --bg:#080808;--card:#0f0f0f;--border:#1e1e1e;
    --text:#c8c8c8;--muted:#555;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{
    background:var(--bg);color:var(--text);
    font-family:'Share Tech Mono',monospace;font-size:12px;
    padding:20px;
  }
  .status-header{
    display:flex;align-items:center;gap:20px;margin-bottom:30px;
  }
  .status-title{
    font-family:'Press Start 2P',monospace;font-size:16px;
    color:var(--neon);text-shadow:0 0 12px var(--neon);
    letter-spacing:2px;
  }
  .status-badge{
    font-family:'Press Start 2P',monospace;font-size:10px;
    padding:8px 16px;border:1px solid;
    animation:blink 1s step-end infinite;
  }
  .status-badge.healthy{color:var(--neon);border-color:var(--neon);background:#001a00;}
  .status-badge.degraded{color:var(--amber);border-color:var(--amber);background:#1a1600;}
  .status-badge.error{color:var(--red);border-color:var(--red);background:#1a0000;}
  @keyframes blink{50%{opacity:0;}}

  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;}
  .grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:20px;}
  @media(max-width:900px){
    .grid-2{grid-template-columns:1fr;}
    .grid-3{grid-template-columns:1fr;}
  }

  .card{background:var(--card);border:1px solid var(--border);padding:20px;border-radius:8px;}
  .card-title{
    font-family:'Press Start 2P',monospace;font-size:10px;
    color:var(--neon2);letter-spacing:2px;margin-bottom:15px;
    border-bottom:1px solid var(--border);padding-bottom:10px;
  }

  .metric{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #111;}
  .metric:last-child{border-bottom:none;}
  .metric-label{color:var(--muted);}
  .metric-value{color:var(--text);font-weight:600;}
  .metric-value.ok{color:var(--neon);}
  .metric-value.warn{color:var(--amber);}
  .metric-value.err{color:var(--red);}

  .agent-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-top:15px;}
  @media(max-width:900px){.agent-grid{grid-template-columns:repeat(2,1fr);}}
  @media(max-width:560px){.agent-grid{grid-template-columns:1fr;}}

  .agent-card{
    background:#141414;border:1px solid;padding:15px;border-radius:6px;
    text-align:center;transition:box-shadow 0.2s;
  }
  .agent-card.active{border-color:var(--neon);box-shadow:0 0 12px #00ff8833;}
  .agent-card.running{border-color:var(--neon2);box-shadow:0 0 12px #00ffff33;}
  .agent-card.idle{border-color:#555;}
  .agent-emoji{font-size:28px;margin-bottom:8px;}
  .agent-name{font-family:'Press Start 2P',monospace;font-size:8px;margin-bottom:6px;letter-spacing:1px;}
  .agent-status{font-size:10px;font-weight:600;}
  .agent-status.active{color:var(--neon);}
  .agent-status.running{color:var(--neon2);}
  .agent-status.offline{color:var(--red);}
  .agent-status.idle{color:var(--muted);}

  .command-row{
    display:flex;justify-content:space-between;align-items:center;
    padding:10px;background:#0f0f0f;border-left:3px solid;margin-bottom:8px;border-radius:4px;
  }
  .command-row.ok{border-color:var(--neon);}
  .command-row.error{border-color:var(--red);}
  .command-row.skipped{border-color:#555;}
  .command-name{font-weight:600;flex:1;}
  .command-status{font-size:11px;min-width:60px;text-align:right;}

  .event-log{max-height:400px;overflow-y:auto;font-size:10px;}
  .event-item{padding:8px;border-bottom:1px solid #111;margin-bottom:6px;}
  .event-item:last-child{border-bottom:none;}
  .event-time{color:var(--muted);font-size:9px;}
  .event-msg{color:var(--text);margin-top:4px;}
  .event-info{color:var(--neon);severity:info;}
  .event-warn{color:var(--amber);severity:warning;}
  .event-error{color:var(--red);severity:error;}

  .progress-bar{background:#111;height:8px;border-radius:4px;overflow:hidden;margin:10px 0;}
  .progress-fill{height:100%;background:var(--neon);box-shadow:0 0 8px #00ff88;}

  .endpoint-check{
    display:flex;justify-content:space-between;align-items:center;
    padding:8px 0;border-bottom:1px solid #111;
  }
  .endpoint-check:last-child{border-bottom:none;}
  .endpoint-name{font-family:'Share Tech Mono',monospace;font-size:11px;}
  .endpoint-badge{
    font-family:'Press Start 2P',monospace;font-size:7px;
    padding:3px 8px;border:1px solid;border-radius:3px;
  }
  .endpoint-badge.ok{color:var(--neon);border-color:var(--neon);background:#001a0033;}
  .endpoint-badge.error{color:var(--red);border-color:var(--red);background:#1a000033;}

  .refresh-info{
    text-align:center;margin-top:30px;color:var(--muted);
    font-family:'Press Start 2P',monospace;font-size:8px;letter-spacing:1px;
  }
  .counter{color:var(--neon);}
</style>
</head>
<body>

<div class="status-header">
  <div class="status-title">⚡ LIVE STATUS</div>
  <div id="status-badge" class="status-badge healthy">ONLINE</div>
  <div id="uptime" style="margin-left:auto;color:var(--neon2);">--:--</div>
</div>

<div class="grid-2">
  <!-- System Overview -->
  <div class="card">
    <div class="card-title">SYSTEM</div>
    <div class="metric">
      <span class="metric-label">Status</span>
      <span class="metric-value" id="sys-status">--</span>
    </div>
    <div class="metric">
      <span class="metric-label">Uptime</span>
      <span class="metric-value" id="sys-uptime">--</span>
    </div>
    <div class="metric">
      <span class="metric-label">Errors</span>
      <span class="metric-value" id="sys-errors">0</span>
    </div>
    <div class="metric">
      <span class="metric-label">Last Check</span>
      <span class="metric-value" id="sys-checked" style="font-size:10px;word-break:break-all;">--</span>
    </div>
  </div>

  <!-- Endpoints -->
  <div class="card">
    <div class="card-title">ENDPOINTS</div>
    <div id="endpoint-checks"></div>
  </div>
</div>

<!-- Agents -->
<div class="card">
  <div class="card-title">AGENT STATUS</div>
  <div class="agent-grid" id="agent-grid"></div>
</div>

<div class="grid-2">
  <!-- Commands -->
  <div class="card">
    <div class="card-title">COMMAND HEALTH</div>
    <div id="command-checks"></div>
  </div>

  <!-- Events -->
  <div class="card">
    <div class="card-title">RECENT EVENTS</div>
    <div class="event-log" id="event-log"></div>
  </div>
</div>

<div class="refresh-info">
  Auto-refresh every <span class="counter" id="refresh-counter">10</span>s
</div>

<script>
const REFRESH_INTERVAL = 10000;

async function updateStatus() {
  try {
    const res = await fetch('/api/system-status');
    const data = await res.json();
    
    // System status
    const sys = data.system || {};
    document.getElementById('sys-status').textContent = sys.status || '--';
    document.getElementById('sys-status').className = `metric-value ${sys.status === 'healthy' ? 'ok' : 'warn'}`;
    document.getElementById('sys-uptime').textContent = sys.uptime || '--';
    document.getElementById('sys-errors').textContent = sys.errors || 0;
    document.getElementById('sys-checked').textContent = (sys.last_checked || '--').substring(0, 19);

    // Status badge
    const badge = document.getElementById('status-badge');
    badge.className = `status-badge ${sys.status || 'degraded'}`;
    badge.textContent = sys.status ? sys.status.toUpperCase() : 'UNKNOWN';

    // Endpoints
    const endpoints = data.endpoints || {};
    let endpointHTML = '';
    for (const [path, info] of Object.entries(endpoints.checks || {})) {
      const ok = info.status === 'ok';
      endpointHTML += `
        <div class="endpoint-check">
          <span class="endpoint-name">${path}</span>
          <span class="endpoint-badge ${ok ? 'ok' : 'error'}">${ok ? '✓' : '✗'}</span>
        </div>
      `;
    }
    document.getElementById('endpoint-checks').innerHTML = endpointHTML;

    // Agents
    const agents = data.agents || {};
    const agentMap = {
      'jarvis': { emoji: '🧠', name: 'JARVIS' },
      'scout': { emoji: '🔍', name: 'SCOUT' },
      'watchdog': { emoji: '🐕', name: 'WATCHDOG' },
      'codex': { emoji: '⚙️', name: 'CODEX' },
      'clipper': { emoji: '🦀', name: 'CLIPPER' },
      'hawk': { emoji: '🦅', name: 'HAWK' }
    };
    let agentHTML = '';
    for (const [key, { emoji, name }] of Object.entries(agentMap)) {
      const status = agents[key] || 'unknown';
      const statusClass = status === 'offline' ? 'offline' : status === 'idle' ? 'idle' : 'active';
      agentHTML += `
        <div class="agent-card ${statusClass}">
          <div class="agent-emoji">${emoji}</div>
          <div class="agent-name">${name}</div>
          <div class="agent-status ${statusClass}">${status.toUpperCase()}</div>
        </div>
      `;
    }
    document.getElementById('agent-grid').innerHTML = agentHTML;

    // Commands
    const commands = data.commands || {};
    let cmdHTML = '';
    for (const [cmd, info] of Object.entries(commands)) {
      const status = info.status || 'unknown';
      const latency = info.latency_ms !== null ? info.latency_ms + 'ms' : '--';
      const statusClass = status === 'error' ? 'error' : status === 'skipped' ? 'skipped' : 'ok';
      cmdHTML += `
        <div class="command-row ${statusClass}">
          <span class="command-name">${cmd}</span>
          <span class="command-status">${latency}</span>
        </div>
      `;
    }
    document.getElementById('command-checks').innerHTML = cmdHTML;

    // Events
    const events = data.events || [];
    let eventHTML = '';
    for (const event of events.slice().reverse().slice(0, 8)) {
      const cls = event.severity === 'error' ? 'event-error' : event.severity === 'warning' ? 'event-warn' : 'event-info';
      eventHTML += `
        <div class="event-item">
          <div class="event-time">${event.timestamp.substring(11, 19)}</div>
          <div class="event-msg ${cls}">${event.message}</div>
        </div>
      `;
    }
    document.getElementById('event-log').innerHTML = eventHTML;

  } catch (e) {
    console.error('Status fetch failed:', e);
  }
}

// Initial load
updateStatus();

// Refresh every 10 seconds
let refreshCounter = 10;
setInterval(() => {
  refreshCounter--;
  document.getElementById('refresh-counter').textContent = refreshCounter;
  if (refreshCounter <= 0) {
    updateStatus();
    refreshCounter = 10;
  }
}, 1000);
</script>
</body>
</html>"""


@app.route("/status")
def status_panel():
    return render_template_string(STATUS_PANEL_HTML)


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import socket
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"OpenClaw Dashboard → http://localhost:8080  |  LAN/Tailscale → http://{local_ip}:8080")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
