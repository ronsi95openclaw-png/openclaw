"""
Google Sheets Trade Logger + Report Agent — ClawBot
=====================================================
Logs every trade to Google Sheets, analyzes performance,
and uses LLM to generate improvement recommendations.

Setup (one-time):
  1. GCP Console → Create Project → Enable Google Sheets API
  2. IAM → Service Accounts → Create → Download JSON key
  3. Open the Google Sheet → Share with service account email
  4. Add to .env:
       GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service_account.json
       GOOGLE_SHEET_NAME=OpenClaw Trades

Sheet tabs created automatically:
  - Trades     — raw trade log (timestamp, coin, action, qty, price, pnl, confidence)
  - Analysis   — calculated metrics (win rate, best coin, avg PnL, etc.)
  - Report     — LLM-generated improvement recommendations
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("clawbot.agents.sheets")

_DATA_DIR   = Path(__file__).parent.parent / "data"
_TRADES_LOG = _DATA_DIR / "logs" / "trades.log"
_REPORTS_DIR = _DATA_DIR / "reports"

# Sheet tab names
_TAB_TRADES   = "Trades"
_TAB_ANALYSIS = "Analysis"
_TAB_REPORT   = "Report"

_TRADES_HEADERS = [
    "Timestamp", "Coin", "Action", "Confidence",
    "Entry Price", "Qty", "USD Amount", "PnL USD",
    "Status", "Strategy", "Notes",
]


# ---------------------------------------------------------------------------
# Google Sheets connection
# ---------------------------------------------------------------------------

def _get_client():
    """Return authenticated gspread client or raise if not configured."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise RuntimeError(
            "gspread / google-auth not installed. Run: pip install gspread google-auth"
        )

    sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not sa_path or not Path(sa_path).exists():
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON not set or file not found. "
            "See setup instructions in agents/sheets_agent.py"
        )

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(sa_path, scopes=scopes)
    return gspread.authorize(creds)


def _get_or_create_sheet():
    """Open existing sheet or create a new one with all tabs."""
    client = _get_client()
    sheet_name = os.getenv("GOOGLE_SHEET_NAME", "OpenClaw Trades")

    try:
        spreadsheet = client.open(sheet_name)
        logger.info(f"Opened existing sheet: {sheet_name}")
    except Exception:
        spreadsheet = client.create(sheet_name)
        logger.info(f"Created new sheet: {sheet_name}")

    # Ensure all tabs exist
    existing_tabs = [ws.title for ws in spreadsheet.worksheets()]

    if _TAB_TRADES not in existing_tabs:
        ws = spreadsheet.add_worksheet(_TAB_TRADES, rows=5000, cols=len(_TRADES_HEADERS))
        ws.append_row(_TRADES_HEADERS, value_input_option="RAW")
        # Bold headers
        ws.format("A1:K1", {"textFormat": {"bold": True}})
        logger.info("Created Trades tab")

    if _TAB_ANALYSIS not in existing_tabs:
        spreadsheet.add_worksheet(_TAB_ANALYSIS, rows=50, cols=10)
        logger.info("Created Analysis tab")

    if _TAB_REPORT not in existing_tabs:
        spreadsheet.add_worksheet(_TAB_REPORT, rows=200, cols=5)
        logger.info("Created Report tab")

    return spreadsheet


# ---------------------------------------------------------------------------
# Trade logging
# ---------------------------------------------------------------------------

def log_trade_to_sheet(trade: dict) -> bool:
    """
    Append a single trade to the Trades tab.

    trade dict keys (from executor.py logs):
        timestamp, coin, action, confidence, price, qty, usd_amount, status
    """
    try:
        spreadsheet = _get_or_create_sheet()
        ws = spreadsheet.worksheet(_TAB_TRADES)
        row = [
            trade.get("timestamp", datetime.now(timezone.utc).isoformat()),
            trade.get("coin", ""),
            trade.get("action", ""),
            trade.get("confidence", ""),
            trade.get("price", ""),
            trade.get("qty", ""),
            trade.get("usd_amount", ""),
            trade.get("pnl", ""),
            trade.get("status", ""),
            trade.get("strategy", "RSI+MACD"),
            trade.get("notes", ""),
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Logged trade to sheet: {trade.get('coin')} {trade.get('action')}")
        return True
    except Exception as exc:
        logger.error(f"Failed to log trade to sheet: {exc}")
        return False


def sync_all_trades() -> int:
    """
    Read all trades from data/logs/trades.log and push to Google Sheets.
    Clears existing data and re-syncs everything.
    Returns count of trades synced.
    """
    if not _TRADES_LOG.exists():
        logger.warning("No trades.log found")
        return 0

    trades = []
    for line in _TRADES_LOG.read_text(encoding="utf-8").strip().splitlines():
        try:
            trades.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not trades:
        return 0

    try:
        spreadsheet = _get_or_create_sheet()
        ws = spreadsheet.worksheet(_TAB_TRADES)

        # Clear and re-write headers + all trades
        ws.clear()
        ws.append_row(_TRADES_HEADERS, value_input_option="RAW")
        ws.format("A1:K1", {"textFormat": {"bold": True}})

        rows = []
        for t in trades:
            rows.append([
                t.get("timestamp", ""),
                t.get("coin", ""),
                t.get("action", ""),
                t.get("confidence", ""),
                t.get("price", ""),
                t.get("qty", ""),
                t.get("usd_amount", ""),
                t.get("pnl", ""),
                t.get("status", ""),
                t.get("strategy", "RSI+MACD"),
                t.get("notes", ""),
            ])

        if rows:
            ws.append_rows(rows, value_input_option="USER_ENTERED")

        logger.info(f"Synced {len(trades)} trades to Google Sheets")
        return len(trades)

    except Exception as exc:
        logger.error(f"Sync failed: {exc}")
        return 0


# ---------------------------------------------------------------------------
# Trade analysis
# ---------------------------------------------------------------------------

def analyze_trades() -> dict:
    """
    Analyze trades.log and return performance metrics.
    Works even without Google Sheets configured.
    """
    if not _TRADES_LOG.exists():
        return {"error": "No trades log found", "total_trades": 0}

    trades = []
    for line in _TRADES_LOG.read_text(encoding="utf-8").strip().splitlines():
        try:
            trades.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not trades:
        return {"error": "No trades found in log", "total_trades": 0}

    executed = [t for t in trades if t.get("status") not in ("skipped", "error")]
    skipped  = [t for t in trades if t.get("status") == "skipped"]
    errors   = [t for t in trades if t.get("status") == "error"]

    # PnL analysis (only trades with pnl field)
    pnl_trades = [t for t in executed if t.get("pnl") not in (None, "", 0)]
    total_pnl  = sum(float(t.get("pnl", 0)) for t in pnl_trades)
    wins       = [t for t in pnl_trades if float(t.get("pnl", 0)) > 0]
    losses     = [t for t in pnl_trades if float(t.get("pnl", 0)) <= 0]
    win_rate   = len(wins) / len(pnl_trades) * 100 if pnl_trades else 0

    # Per-coin breakdown
    coins: dict[str, dict] = {}
    for t in trades:
        coin = t.get("coin", "unknown")
        if coin not in coins:
            coins[coin] = {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0}
        coins[coin]["trades"] += 1
        pnl = float(t.get("pnl", 0))
        coins[coin]["pnl"] += pnl
        if pnl > 0:
            coins[coin]["wins"] += 1
        elif pnl < 0:
            coins[coin]["losses"] += 1

    # Best and worst coin
    best_coin  = max(coins.items(), key=lambda x: x[1]["pnl"])[0] if coins else "N/A"
    worst_coin = min(coins.items(), key=lambda x: x[1]["pnl"])[0] if coins else "N/A"

    # Action breakdown
    longs  = [t for t in executed if t.get("action") == "BUY"]
    shorts = [t for t in executed if t.get("action") == "SELL"]

    return {
        "total_trades": len(trades),
        "executed": len(executed),
        "skipped": len(skipped),
        "errors": len(errors),
        "total_pnl_usd": round(total_pnl, 2),
        "win_rate_pct": round(win_rate, 1),
        "wins": len(wins),
        "losses": len(losses),
        "avg_pnl_per_trade": round(total_pnl / len(pnl_trades), 2) if pnl_trades else 0,
        "best_coin": best_coin,
        "worst_coin": worst_coin,
        "longs": len(longs),
        "shorts": len(shorts),
        "coin_breakdown": coins,
    }


# ---------------------------------------------------------------------------
# LLM Report Generation
# ---------------------------------------------------------------------------

_REPORT_SYSTEM = """\
You are a quantitative trading analyst reviewing the performance of ClawBot,
an automated crypto trading bot using RSI+MACD signals.

Analyze the trade metrics provided and generate a concise, actionable report.

Structure your response as:
1. VERDICT — one word: WINNING / LOSING / NEUTRAL
2. KEY WINS — what's working (2-3 bullet points)
3. PROBLEMS — what needs fixing (2-3 bullet points)
4. RECOMMENDATIONS — specific changes to improve performance (3-5 bullet points)
5. PRIORITY ACTION — the single most impactful change to make right now

Be direct. No fluff. Think like a hedge fund risk manager.
"""


def generate_report(analysis: dict) -> str:
    """Use LLM to generate improvement recommendations from trade analysis."""
    if analysis.get("total_trades", 0) == 0:
        return "No trades to analyze yet. Run /autotrade on to start collecting data."

    prompt = (
        f"Analyze these trading bot performance metrics and generate recommendations:\n\n"
        f"Total Trades: {analysis['total_trades']}\n"
        f"Executed: {analysis['executed']} | Skipped: {analysis['skipped']} | Errors: {analysis['errors']}\n"
        f"Total PnL: ${analysis['total_pnl_usd']}\n"
        f"Win Rate: {analysis['win_rate_pct']}% ({analysis['wins']}W / {analysis['losses']}L)\n"
        f"Avg PnL per trade: ${analysis['avg_pnl_per_trade']}\n"
        f"Best coin: {analysis['best_coin']} | Worst coin: {analysis['worst_coin']}\n"
        f"Longs: {analysis['longs']} | Shorts: {analysis['shorts']}\n\n"
        f"Coin breakdown: {json.dumps(analysis['coin_breakdown'], indent=2)}\n\n"
        f"Strategy: RSI(14) + MACD(12,26,9) on Crypto.com, 4H timeframe, 1.5% risk per trade."
    )

    try:
        from ollama import chat as ollama_chat
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
        resp  = ollama_chat(
            model=model,
            messages=[
                {"role": "system", "content": _REPORT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.message.content.strip()
    except Exception as e:
        logger.warning(f"Ollama failed, trying Haiku: {e}")
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                return "LLM unavailable — check Ollama or ANTHROPIC_API_KEY in .env"
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                system=_REPORT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return next((b.text for b in resp.content if b.type == "text"), "").strip()
        except Exception as e2:
            return f"LLM report generation failed: {e2}"


def push_report_to_sheet(analysis: dict, llm_report: str) -> bool:
    """Write analysis metrics + LLM report to the Report tab."""
    try:
        spreadsheet = _get_or_create_sheet()

        # Analysis tab — metrics table
        ws_analysis = spreadsheet.worksheet(_TAB_ANALYSIS)
        ws_analysis.clear()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        ws_analysis.append_row(["Metric", "Value", "Updated"], value_input_option="RAW")
        ws_analysis.format("A1:C1", {"textFormat": {"bold": True}})
        metrics = [
            ["Total Trades", analysis["total_trades"], now],
            ["Executed", analysis["executed"], ""],
            ["Skipped", analysis["skipped"], ""],
            ["Total PnL USD", f"${analysis['total_pnl_usd']}", ""],
            ["Win Rate", f"{analysis['win_rate_pct']}%", ""],
            ["Wins", analysis["wins"], ""],
            ["Losses", analysis["losses"], ""],
            ["Avg PnL/Trade", f"${analysis['avg_pnl_per_trade']}", ""],
            ["Best Coin", analysis["best_coin"], ""],
            ["Worst Coin", analysis["worst_coin"], ""],
            ["Long Trades", analysis["longs"], ""],
            ["Short Trades", analysis["shorts"], ""],
        ]
        ws_analysis.append_rows(metrics, value_input_option="USER_ENTERED")

        # Report tab — LLM report
        ws_report = spreadsheet.worksheet(_TAB_REPORT)
        ws_report.clear()
        ws_report.append_row([f"ClawBot Trade Report — {now}"], value_input_option="RAW")
        ws_report.format("A1", {"textFormat": {"bold": True, "fontSize": 14}})
        ws_report.append_row([""], value_input_option="RAW")
        for line in llm_report.split("\n"):
            ws_report.append_row([line], value_input_option="RAW")

        logger.info("Pushed report to Google Sheets")
        return True

    except Exception as exc:
        logger.error(f"Failed to push report to sheet: {exc}")
        return False


# ---------------------------------------------------------------------------
# Telegram formatter
# ---------------------------------------------------------------------------

def format_telegram_report(analysis: dict, llm_report: str) -> str:
    """Format trade analysis as a Telegram HTML message."""
    if analysis.get("error"):
        return f"📭 <b>No trade data:</b> {analysis['error']}"

    pnl_icon = "📈" if analysis["total_pnl_usd"] >= 0 else "📉"
    win_icon = "🏆" if analysis["win_rate_pct"] >= 50 else "⚠️"

    # Truncate LLM report for Telegram (4096 char limit)
    report_preview = llm_report[:800] + "..." if len(llm_report) > 800 else llm_report

    return (
        f"📊 <b>ClawBot Trade Report</b>\n\n"
        f"{pnl_icon} <b>Total PnL:</b> <code>${analysis['total_pnl_usd']:+.2f}</code>\n"
        f"{win_icon} <b>Win Rate:</b> <code>{analysis['win_rate_pct']}%</code> "
        f"({analysis['wins']}W / {analysis['losses']}L)\n"
        f"📋 <b>Trades:</b> {analysis['total_trades']} total "
        f"({analysis['executed']} exec, {analysis['skipped']} skipped)\n"
        f"🥇 <b>Best Coin:</b> {analysis['best_coin']}\n"
        f"🥴 <b>Worst Coin:</b> {analysis['worst_coin']}\n\n"
        f"<b>🤖 AI Analysis:</b>\n"
        f"<i>{report_preview}</i>\n\n"
        f"<i>Full report saved to Google Sheets.</i>"
    )


# ---------------------------------------------------------------------------
# Main report runner
# ---------------------------------------------------------------------------

async def run_report(bot=None, chat_id: int = 0) -> tuple[str, dict]:
    """
    Full pipeline: analyze → LLM report → push to sheets → return Telegram message.
    Can be used as APScheduler job or called directly from /report command.
    """
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    analysis   = analyze_trades()
    llm_report = generate_report(analysis)

    # Save report locally (always works, even without Sheets)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_file = _REPORTS_DIR / f"trade_report_{now}.md"
    report_file.write_text(
        f"# ClawBot Trade Report — {now}\n\n"
        f"## Metrics\n```\n{json.dumps(analysis, indent=2)}\n```\n\n"
        f"## AI Analysis\n{llm_report}\n",
        encoding="utf-8",
    )
    logger.info(f"Report saved: {report_file}")

    # Push to Google Sheets (optional — skip if not configured)
    sheets_ok = False
    if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip():
        sheets_ok = push_report_to_sheet(analysis, llm_report)

    msg = format_telegram_report(analysis, llm_report)
    if sheets_ok:
        msg += "\n✅ <i>Synced to Google Sheets.</i>"
    else:
        msg += "\n💾 <i>Saved locally (Google Sheets not configured).</i>"

    if bot and chat_id:
        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

    return msg, analysis
