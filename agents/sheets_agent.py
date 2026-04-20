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
    try:
        raw_text = _TRADES_LOG.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(f"Could not read trades log: {exc}")
        return 0
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        raw = line if line.startswith("{") else (line.split("|", 2)[-1].strip() if "|" in line else "")
        try:
            trades.append(json.loads(raw))
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

def analyze_trades(period: str = "all") -> dict:
    """
    Analyze trades.log and return performance metrics.
    Works even without Google Sheets configured.

    period: "today" = last 24h, "week" = last 7 days,
            "month" = last 30 days, "all" = no filter
    """
    if not _TRADES_LOG.exists():
        return {"error": "No trades log found", "total_trades": 0}

    all_trades = []
    for line in _TRADES_LOG.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Support both pure JSONL and legacy "TRADE_DECISION | ts | {...}" format
        if line.startswith("{"):
            raw = line
        elif "|" in line:
            raw = line.split("|", 2)[-1].strip()
        else:
            continue
        try:
            all_trades.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    if not all_trades:
        return {"error": "No trades found in log", "total_trades": 0}

    # --- Period filtering ---
    now_utc = datetime.now(timezone.utc)
    period = period.lower() if period else "all"
    if period == "today":
        from datetime import timedelta
        cutoff = now_utc - timedelta(hours=24)
    elif period == "week":
        from datetime import timedelta
        cutoff = now_utc - timedelta(days=7)
    elif period == "month":
        from datetime import timedelta
        cutoff = now_utc - timedelta(days=30)
    else:
        period = "all"
        cutoff = None

    trades = []
    for t in all_trades:
        if cutoff is not None:
            ts_raw = t.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass  # keep trade if timestamp can't be parsed
        trades.append(t)

    if not trades:
        return {
            "error": f"No trades found for period: {period}",
            "total_trades": 0,
            "period": period,
        }

    # Date range string
    timestamps = []
    for t in trades:
        ts_raw = t.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            timestamps.append(ts)
        except (ValueError, TypeError):
            pass
    if timestamps:
        date_range = (
            f"{min(timestamps).strftime('%Y-%m-%d')} to "
            f"{max(timestamps).strftime('%Y-%m-%d')}"
        )
    else:
        date_range = "unknown"

    executed = [t for t in trades if t.get("status") not in ("skipped", "error")]
    skipped  = [t for t in trades if t.get("status") == "skipped"]
    errors   = [t for t in trades if t.get("status") == "error"]

    # Action breakdown
    buy_count  = sum(1 for t in executed if t.get("action") == "BUY")
    sell_count = sum(1 for t in executed if t.get("action") == "SELL")

    # PnL analysis — treat missing/null/empty PnL as 0
    def _pnl(t: dict) -> float:
        v = t.get("pnl", 0)
        try:
            return float(v) if v not in (None, "") else 0.0
        except (ValueError, TypeError):
            return 0.0

    pnl_trades = [t for t in executed if _pnl(t) != 0.0]
    total_pnl  = sum(_pnl(t) for t in executed)
    wins       = [t for t in executed if _pnl(t) > 0]
    losses     = [t for t in executed if _pnl(t) < 0]
    win_rate   = len(wins) / len(executed) * 100 if executed else 0

    # Best and worst individual trade
    def _trade_label(t: dict) -> dict:
        ts_raw = t.get("timestamp", "")
        try:
            date_str = datetime.fromisoformat(
                str(ts_raw).replace("Z", "+00:00")
            ).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_str = str(ts_raw)[:10] if ts_raw else "unknown"
        return {"coin": t.get("coin", "?"), "pnl": round(_pnl(t), 2), "date": date_str}

    best_trade  = _trade_label(max(executed, key=_pnl)) if executed else {"coin": "N/A", "pnl": 0.0, "date": "—"}
    worst_trade = _trade_label(min(executed, key=_pnl)) if executed else {"coin": "N/A", "pnl": 0.0, "date": "—"}

    # Per-coin breakdown
    coins: dict[str, dict] = {}
    for t in trades:
        coin = t.get("coin", "unknown")
        if coin not in coins:
            coins[coin] = {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0}
        coins[coin]["trades"] += 1
        pnl = _pnl(t)
        coins[coin]["pnl"] += pnl
        if pnl > 0:
            coins[coin]["wins"] += 1
        elif pnl < 0:
            coins[coin]["losses"] += 1

    # Build by_coin with win_rate
    by_coin = {
        coin: {
            "trades": data["trades"],
            "pnl": round(data["pnl"], 2),
            "win_rate": round(
                data["wins"] / data["trades"] * 100 if data["trades"] else 0, 1
            ),
        }
        for coin, data in coins.items()
    }

    # Legacy fields kept for backward compat (push_report_to_sheet uses them)
    best_coin  = max(coins.items(), key=lambda x: x[1]["pnl"])[0] if coins else "N/A"
    worst_coin = min(coins.items(), key=lambda x: x[1]["pnl"])[0] if coins else "N/A"

    return {
        # New fields
        "period": period,
        "date_range": date_range,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "win_rate": round(win_rate, 1),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "avg_pnl_per_trade": round(total_pnl / len(executed), 2) if executed else 0,
        "by_coin": by_coin,
        # Legacy / shared fields
        "total_trades": len(trades),
        "executed": len(executed),
        "skipped": len(skipped),
        "errors": len(errors),
        "total_pnl_usd": round(total_pnl, 2),
        "win_rate_pct": round(win_rate, 1),  # kept for backward compat
        "wins": len(wins),
        "losses": len(losses),
        "best_coin": best_coin,
        "worst_coin": worst_coin,
        "longs": buy_count,
        "shorts": sell_count,
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
        model = os.getenv("OLLAMA_MODEL", "gemma3")
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
    """Format trade analysis as a beautiful Telegram HTML message with tree layout."""
    if analysis.get("error"):
        return f"📭 <b>No trade data:</b> {analysis['error']}"

    period_label = {
        "today": "Today",
        "week":  "Last 7 Days",
        "month": "Last 30 Days",
        "all":   "All Time",
    }.get(analysis.get("period", "all"), "All Time")

    total   = analysis["total_trades"]
    buys    = analysis.get("buy_count", analysis.get("longs", 0))
    sells   = analysis.get("sell_count", analysis.get("shorts", 0))
    wins    = analysis["wins"]
    wr      = analysis.get("win_rate", analysis.get("win_rate_pct", 0))
    pnl     = analysis["total_pnl_usd"]
    avg_pnl = analysis.get("avg_pnl_per_trade", 0)

    pnl_sign = "+" if pnl >= 0 else ""
    avg_sign = "+" if avg_pnl >= 0 else ""

    # Best / worst trade
    best  = analysis.get("best_trade",  {"coin": analysis.get("best_coin", "N/A"),  "pnl": 0.0, "date": "—"})
    worst = analysis.get("worst_trade", {"coin": analysis.get("worst_coin", "N/A"), "pnl": 0.0, "date": "—"})
    best_str  = f"{best['coin']} {'+' if best['pnl'] >= 0 else ''}{best['pnl']:.2f} ({best['date']})"
    worst_str = f"{worst['coin']} {'+' if worst['pnl'] >= 0 else ''}{worst['pnl']:.2f} ({worst['date']})"

    # By-coin section (up to 5 coins, sorted by PnL descending)
    by_coin = analysis.get("by_coin", {})
    sorted_coins = sorted(by_coin.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]
    coin_lines = []
    for i, (coin, data) in enumerate(sorted_coins):
        prefix = "└" if i == len(sorted_coins) - 1 else "├"
        sign   = "+" if data["pnl"] >= 0 else ""
        coin_lines.append(
            f"{prefix} {coin}: {data['trades']} trades · Win {data['win_rate']}% · {sign}${data['pnl']:.2f}"
        )
    coin_section = "\n".join(coin_lines) if coin_lines else "└ No coin data yet"

    # Truncate LLM report to 600 chars
    report_preview = llm_report[:600].rstrip()
    if len(llm_report) > 600:
        report_preview += "…"

    return (
        f"📊 <b>Trade Report — {period_label}</b>\n\n"
        f"📅 Period: {analysis.get('date_range', '—')}\n"
        f"📈 Total Trades: {total} ({buys} BUY · {sells} SELL)\n"
        f"🏆 Win Rate: {wr}% ({wins}/{total} profitable)\n\n"
        f"<b>💰 P&amp;L Summary</b>\n"
        f"├ Total: {pnl_sign}${pnl:.2f}\n"
        f"├ Avg/trade: {avg_sign}${avg_pnl:.2f}\n"
        f"├ Best: {best_str}\n"
        f"└ Worst: {worst_str}\n\n"
        f"<b>📊 By Coin</b>\n"
        f"{coin_section}\n\n"
        f"<b>🤖 AI Analysis</b>\n"
        f"<i>{report_preview}</i>\n\n"
        f"💾 <i>Saved locally</i>"
    )


# ---------------------------------------------------------------------------
# Main report runner
# ---------------------------------------------------------------------------

async def run_report(bot=None, chat_id: int = 0, period: str = "all") -> tuple[str, dict]:
    """
    Full pipeline: analyze → LLM report → push to sheets → return Telegram message.
    Can be used as APScheduler job or called directly from /report command.
    """
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    analysis   = analyze_trades(period=period)
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
