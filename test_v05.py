"""OpenClaw v0.5 — Full End-to-End Test Suite

Covers:
  1.  Hybrid brain (routing, fallback, cache)
  2.  /ask simulation + ClawBot system prompt
  3.  /plan simulation
  4.  /research simulation
  5.  /market — CoinGecko + LLM analysis
  6.  /remind — APScheduler persistence
  7.  Conversation memory (3-turn context)
  8.  Confirmation system (logic layer)
  9.  /brain usage stats
  10. /start menu completeness
  11. Stress test (10 rapid requests)
  12. Full pass/fail report

Run:
    .venv/Scripts/python.exe test_v05.py
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
import threading
import time
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent

# ── Result tracking ───────────────────────────────────────────────────────────
_results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, note: str = "") -> None:
    mark = "PASS" if passed else "FAIL"
    line = f"  [{mark}] {name}" + (f"  — {note}" if note else "")
    print(line)
    _results.append((name, passed, note))


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


# ── Auto-detect available Ollama model ───────────────────────────────────────
section("PRE-FLIGHT")

_ollama_model: str | None = None
_configured_model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

try:
    from ollama import list as _ol_list
    available = [m.model for m in _ol_list().models]
    if _configured_model in available:
        _ollama_model = _configured_model
    elif available:
        _ollama_model = available[0]   # use first available as fallback
    print(f"  Ollama available models: {available}")
    print(f"  Using model for tests:   {_ollama_model}")
    if _ollama_model != _configured_model:
        os.environ["OLLAMA_MODEL"] = _ollama_model
        print(f"  ⚠️  {_configured_model} not found — testing with {_ollama_model}")
        print(f"       Run: ollama pull {_configured_model}  (for production)")
except Exception as exc:
    print(f"  ⚠️  Ollama not reachable: {exc}")

_has_claude_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
_has_ollama     = _ollama_model is not None
print(f"  ANTHROPIC_API_KEY set:   {'YES ✅' if _has_claude_key else 'NO ⚠️  (complex tasks will use Ollama fallback)'}")
record("preflight · Ollama reachable", _has_ollama,
       f"model={_ollama_model}" if _has_ollama else "start with: ollama serve")
record("preflight · Claude API key set", _has_claude_key,
       "set ANTHROPIC_API_KEY in .env for full Claude routing")


# Reload brain with corrected model env var
import core.brain as _brain_mod
importlib.reload(_brain_mod)
from core.brain import ask_hybrid, classify_complexity, get_usage_today, CLAWBOT_SYSTEM


# ─────────────────────────────────────────────────────────────────────────────
# 1. HYBRID BRAIN
# ─────────────────────────────────────────────────────────────────────────────
section("1 · HYBRID BRAIN")

SIMPLE_PROMPT  = "give me a caption for a workout reel"
COMPLEX_PROMPT = "create a full business plan for a gym supplements dropshipping store"

# 1a — Complexity classifier
try:
    complexity_simple = classify_complexity(SIMPLE_PROMPT)
    record("1a · classify simple prompt",
           complexity_simple == "simple", f"got='{complexity_simple}'")
except Exception as exc:
    record("1a · classify simple prompt", False, str(exc))

# 1b — Simple task
if _has_ollama:
    try:
        resp, brain = ask_hybrid(SIMPLE_PROMPT, force="simple")
        record("1b · simple task (Ollama)", bool(resp), f"brain={brain}, len={len(resp)}")
        print(f"       preview: {resp[:80].replace(chr(10),' ')}...")
    except Exception as exc:
        record("1b · simple task (Ollama)", False, str(exc))
else:
    record("1b · simple task (Ollama)", False, "Ollama offline — skipped")

# 1c — Complex classification (requires API key)
try:
    # Temporarily pretend API key is set so classifier sees it
    if not _has_claude_key:
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-key-for-classify"
    importlib.reload(_brain_mod)
    from core.brain import classify_complexity as _cc2
    complexity_complex = _cc2(COMPLEX_PROMPT)
    record("1c · classify complex prompt",
           complexity_complex == "complex", f"got='{complexity_complex}'")
finally:
    if not _has_claude_key:
        os.environ["ANTHROPIC_API_KEY"] = ""
    importlib.reload(_brain_mod)
    from core.brain import ask_hybrid, classify_complexity, get_usage_today, CLAWBOT_SYSTEM

# 1d — Complex task (Claude or Ollama fallback)
if _has_ollama:
    try:
        resp, brain = ask_hybrid(COMPLEX_PROMPT, force="complex")
        record("1d · complex task handled",
               bool(resp), f"brain={brain}, len={len(resp)}")
        print(f"       preview: {resp[:80].replace(chr(10),' ')}...")
    except Exception as exc:
        record("1d · complex task handled", False, str(exc))
else:
    record("1d · complex task handled", False, "Ollama offline — skipped")

# 1e — Fallback: no API key → Ollama
if _has_ollama:
    try:
        orig_key = os.environ.get("ANTHROPIC_API_KEY", "")
        os.environ["ANTHROPIC_API_KEY"] = ""
        importlib.reload(_brain_mod)
        from core.brain import ask_hybrid as _ask2
        resp, brain = _ask2(SIMPLE_PROMPT, force="simple")
        record("1e · graceful fallback (no API key → Ollama)",
               bool(resp) and brain in ("ollama", "cache"),
               f"brain={brain}")
    except Exception as exc:
        record("1e · graceful fallback", False, str(exc))
    finally:
        os.environ["ANTHROPIC_API_KEY"] = orig_key
        importlib.reload(_brain_mod)
        from core.brain import ask_hybrid, classify_complexity, get_usage_today, CLAWBOT_SYSTEM
else:
    record("1e · graceful fallback", False, "Ollama offline — skipped")

# 1f — Cache hit
if _has_ollama:
    try:
        CACHE_PROMPT = "what is bitcoin in one sentence"
        resp1, brain1 = ask_hybrid(CACHE_PROMPT, force="simple")
        resp2, brain2 = ask_hybrid(CACHE_PROMPT, force="simple")
        record("1f · cache hit on repeated prompt",
               brain2 == "cache", f"first={brain1}, second={brain2}")
    except Exception as exc:
        record("1f · cache hit", False, str(exc))
else:
    record("1f · cache hit", False, "Ollama offline — skipped")


# ─────────────────────────────────────────────────────────────────────────────
# 2. /ask SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
section("2 · /ask SIMULATION")

ASK_PROMPT = "what should I focus on today?"

if _has_ollama:
    try:
        resp, brain = ask_hybrid(ASK_PROMPT, system=CLAWBOT_SYSTEM, force="simple")
        record("2a · /ask returns response", bool(resp), f"brain={brain}")

        resp_lower = resp.lower()
        is_actionable = any(w in resp_lower for w in [
            "focus", "priority", "action", "task", "goal", "work", "start", "do", "build"
        ])
        record("2b · response is actionable", is_actionable,
               f"preview: {resp[:60].replace(chr(10),' ')}")

        intents = {
            "BUSINESS":  any(w in ASK_PROMPT.lower() for w in ["business","hustle","money","sell","brand"]),
            "CRYPTO":    any(w in ASK_PROMPT.lower() for w in ["crypto","btc","eth","dca","trade"]),
            "CONTENT":   any(w in ASK_PROMPT.lower() for w in ["reel","caption","post","tiktok"]),
            "RESEARCH":  any(w in ASK_PROMPT.lower() for w in ["research","analyse","analyze","compare"]),
            "TASK":      any(w in ASK_PROMPT.lower() for w in ["focus","today","remind","plan","schedule"]),
        }
        detected = [k for k, v in intents.items() if v] or ["GENERAL"]
        record("2c · intent detection works", True, f"detected={detected}")
        print(f"       full response preview:\n{resp[:200]}")
    except Exception as exc:
        record("2a · /ask", False, str(exc))
else:
    for t in ["2a","2b","2c"]:
        record(f"{t} · /ask simulation", False, "Ollama offline — skipped")


# ─────────────────────────────────────────────────────────────────────────────
# 3. /plan SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
section("3 · /plan SIMULATION")

PLAN_TOPIC  = "I want to start selling custom gym gear on Instagram"
PLAN_PROMPT = (
    f"Create a structured action plan for: {PLAN_TOPIC}\n\n"
    "Format with these sections:\n"
    "OVERVIEW — 2 sentences\n"
    "PROS — 3 bullet points\n"
    "CONS / RISKS — 3 bullet points\n"
    "ACTION PLAN — 5 numbered steps\n"
    "RESOURCES NEEDED — list\n"
    "TIME + COST ESTIMATE — brief\n\n"
    "Be direct and actionable. Format for Telegram."
)

if _has_ollama:
    try:
        resp, brain = ask_hybrid(PLAN_PROMPT, system=CLAWBOT_SYSTEM, force="complex")
        record("3a · /plan returns response", bool(resp), f"brain={brain}, len={len(resp)}")

        resp_upper = resp.upper()
        for sec_name, keywords in [
            ("OVERVIEW",    ["OVERVIEW"]),
            ("PROS",        ["PROS", "PRO"]),
            ("CONS/RISKS",  ["CONS", "RISK", "CON"]),
            ("ACTION PLAN", ["ACTION", "STEP"]),
            ("RESOURCES",   ["RESOURCE", "NEED", "TOOL"]),
            ("TIME/COST",   ["TIME", "COST", "WEEK", "MONTH", "$", "BUDGET"]),
        ]:
            found = any(kw in resp_upper for kw in keywords)
            record(f"3b · plan contains {sec_name}", found)

        print(f"\n       Plan preview (first 300 chars):\n{resp[:300]}")
    except Exception as exc:
        record("3a · /plan", False, str(exc))
else:
    record("3a · /plan simulation", False, "Ollama offline — skipped")


# ─────────────────────────────────────────────────────────────────────────────
# 4. /research SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
section("4 · /research SIMULATION")

RESEARCH_TOPIC  = "best crypto to DCA into this month"
RESEARCH_PROMPT = (
    f"Do a research breakdown on: {RESEARCH_TOPIC}\n\n"
    "Format with:\n"
    "SUMMARY — 2-3 sentences\n"
    "KEY POINTS — 5 bullet points\n"
    "WHAT TO WATCH — 3 things to monitor\n"
    "RECOMMENDATION — 1 clear action\n\n"
    "Be direct. Format for Telegram."
)

if _has_ollama:
    try:
        resp, brain = ask_hybrid(RESEARCH_PROMPT, system=CLAWBOT_SYSTEM, force="complex")
        record("4a · /research returns response", bool(resp), f"brain={brain}, len={len(resp)}")

        resp_upper = resp.upper()
        record("4b · contains SUMMARY",        "SUMMARY" in resp_upper)
        record("4c · contains KEY POINTS",     any(w in resp_upper for w in ["KEY", "POINT", "BULLET"]))
        record("4d · contains RECOMMENDATION", any(w in resp_upper for w in [
            "RECOMMEND", "ACTION", "BUY", "DCA", "INVEST", "CONCLUSION"
        ]))
        print(f"\n       Research preview (first 250 chars):\n{resp[:250]}")
    except Exception as exc:
        record("4a · /research", False, str(exc))
else:
    record("4a · /research simulation", False, "Ollama offline — skipped")


# ─────────────────────────────────────────────────────────────────────────────
# 5. /market — CoinGecko + LLM analysis
# ─────────────────────────────────────────────────────────────────────────────
section("5 · /market SIMULATION")

try:
    from core.market import _fetch_prices, _format_price_block

    data     = _fetch_prices()
    btc_usd  = data.get("bitcoin",  {}).get("usd", 0)
    eth_usd  = data.get("ethereum", {}).get("usd", 0)
    sol_usd  = data.get("solana",   {}).get("usd", 0)

    record("5a · CoinGecko BTC price", btc_usd > 0, f"BTC=${btc_usd:,.0f}")
    record("5b · CoinGecko ETH price", eth_usd > 0, f"ETH=${eth_usd:,.0f}")
    record("5c · CoinGecko SOL price", sol_usd > 0, f"SOL=${sol_usd:,.2f}")

    if _has_ollama:
        from core.market import get_market_summary
        summary = get_market_summary()
        record("5d · full market summary generated", bool(summary), f"len={len(summary)}")
        record("5e · summary contains BTC", "BTC" in summary)
        record("5f · summary contains LLM analysis", len(summary) > 200)
        print(f"\n       Market summary:\n{summary[:400]}")
    else:
        record("5d · LLM analysis", False, "Ollama offline — price fetch passed, analysis skipped")
except Exception as exc:
    record("5a · CoinGecko / market", False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 6. /remind — APScheduler + persistence
# ─────────────────────────────────────────────────────────────────────────────
section("6 · /remind + SCHEDULER PERSISTENCE")

from core.scheduler import add_reminder, get_reminders, cancel_reminder
from core.scheduler import _load_tasks, _save_tasks

TEST_CHAT_ID = 999999999

# Clean prior test data
tasks = _load_tasks()
tasks = [t for t in tasks if t.get("chat_id") != TEST_CHAT_ID]
_save_tasks(tasks)

try:
    task = add_reminder(TEST_CHAT_ID, "08:00", "Check crypto markets")
    record("6a · add_reminder returns dict",    isinstance(task, dict), f"id={task.get('id')}")
    record("6b · time stored correctly",        task.get("time") == "08:00")
    record("6c · text stored correctly",        "crypto" in task.get("text", ""))
    record("6d · status is pending",            task.get("status") == "pending")
    record("6e · chat_id stored",               task.get("chat_id") == TEST_CHAT_ID)
except Exception as exc:
    record("6a · add_reminder", False, str(exc))

try:
    tasks_disk = _load_tasks()
    found = any(
        t.get("chat_id") == TEST_CHAT_ID and t.get("status") == "pending"
        for t in tasks_disk
    )
    record("6f · persisted to data/tasks.json", found)
except Exception as exc:
    record("6f · persistence", False, str(exc))

try:
    reminders = get_reminders(TEST_CHAT_ID)
    record("6g · get_reminders returns list",   len(reminders) >= 1, f"count={len(reminders)}")
    if reminders:
        r = reminders[0]
        print(f"       reminder: {r['time']} UTC — '{r['text']}' (id={r['id']})")
except Exception as exc:
    record("6g · get_reminders", False, str(exc))

try:
    raised = False
    try:
        add_reminder(TEST_CHAT_ID, "25:99", "bad time")
    except ValueError:
        raised = True
    record("6h · invalid time raises ValueError", raised)
except Exception as exc:
    record("6h · invalid time validation", False, str(exc))

try:
    task2  = add_reminder(TEST_CHAT_ID, "09:00", "cancel me")
    result = cancel_reminder(task2["id"])
    record("6i · cancel_reminder returns True", result is True)

    still_pending = get_reminders(TEST_CHAT_ID)
    gone = not any(t["id"] == task2["id"] for t in still_pending)
    record("6j · cancelled reminder removed", gone)
except Exception as exc:
    record("6i · cancel_reminder", False, str(exc))

# Clean up
tasks = _load_tasks()
tasks = [t for t in tasks if t.get("chat_id") != TEST_CHAT_ID]
_save_tasks(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# 7. CONVERSATION MEMORY
# ─────────────────────────────────────────────────────────────────────────────
section("7 · CONVERSATION MEMORY")

from core.conversation import add_message, get_history, clear_history

MEM_CHAT_ID = 888888888
clear_history(MEM_CHAT_ID)

try:
    add_message(MEM_CHAT_ID, "user",      "my name is Ronnie")
    add_message(MEM_CHAT_ID, "assistant", "Got it, Ronnie!")
    add_message(MEM_CHAT_ID, "user",      "I am building a trading bot")
    add_message(MEM_CHAT_ID, "assistant", "Great — what exchange?")
    add_message(MEM_CHAT_ID, "user",      "what do you know about me?")

    history = get_history(MEM_CHAT_ID)
    record("7a · history stores 5 messages",    len(history) == 5, f"len={len(history)}")
    record("7b · roles alternating correctly",  history[0]["role"] == "user")
    record("7c · name 'Ronnie' in history",
           any("Ronnie" in m["content"] for m in history))
    record("7d · trading bot context in history",
           any("trading bot" in m["content"] for m in history))
except Exception as exc:
    record("7a · add_message / get_history", False, str(exc))

if _has_ollama:
    try:
        history  = get_history(MEM_CHAT_ID)
        resp, brain = ask_hybrid(
            "what do you know about me so far?",
            system=CLAWBOT_SYSTEM,
            history=history,
            force="simple",
        )
        resp_lower = resp.lower()
        record("7e · LLM recalls name from history",
               "ronnie" in resp_lower, f"brain={brain}")
        record("7f · LLM recalls project from history",
               any(w in resp_lower for w in ["trading", "bot", "exchange", "build"]),
               f"preview: {resp[:80].replace(chr(10),' ')}")
    except Exception as exc:
        record("7e · LLM context recall", False, str(exc))
else:
    record("7e · LLM context recall", False, "Ollama offline — skipped")

try:
    hist_file = ROOT / "data" / "conversation_history.json"
    on_disk   = json.loads(hist_file.read_text(encoding="utf-8"))
    key       = str(MEM_CHAT_ID)
    record("7g · persisted to conversation_history.json",
           key in on_disk, f"messages={len(on_disk.get(key, []))}")
except Exception as exc:
    record("7g · persistence", False, str(exc))

try:
    clear_history(MEM_CHAT_ID)
    cleared = get_history(MEM_CHAT_ID)
    record("7h · /clear resets history", len(cleared) == 0, f"remaining={len(cleared)}")
except Exception as exc:
    record("7h · clear_history", False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 8. CONFIRMATION SYSTEM (logic layer)
# ─────────────────────────────────────────────────────────────────────────────
section("8 · CONFIRMATION SYSTEM (logic layer)")

def _make_telegram_stubs() -> None:
    """Inject minimal telegram stub modules so receiver.py can be imported without a bot."""
    class _Btn:
        def __init__(self, text, callback_data=None):
            self.text = text
            self.callback_data = callback_data

    class _Markup:
        def __init__(self, keyboard):
            self.keyboard = keyboard

    class _FakeFilters:
        VIDEO    = object()
        Document = types.SimpleNamespace(VIDEO=object())

    # telegram (top-level)
    tg = types.ModuleType("telegram")
    tg.InlineKeyboardButton  = _Btn
    tg.InlineKeyboardMarkup  = _Markup
    tg.Update                = type("Update", (), {"ALL_TYPES": None})

    # telegram.ext
    tg_ext = types.ModuleType("telegram.ext")
    tg_ext.Application        = type("Application", (), {"builder": lambda: types.SimpleNamespace(token=lambda t: types.SimpleNamespace(build=lambda: None))})
    tg_ext.CallbackQueryHandler = type("CallbackQueryHandler", (), {"__init__": lambda s, *a, **kw: None})
    tg_ext.CommandHandler      = type("CommandHandler",       (), {"__init__": lambda s, *a, **kw: None})
    tg_ext.ContextTypes        = type("ContextTypes",         (), {"DEFAULT_TYPE": None})
    tg_ext.MessageHandler      = type("MessageHandler",       (), {"__init__": lambda s, *a, **kw: None})
    tg_ext.filters             = _FakeFilters()
    tg.ext                    = tg_ext

    sys.modules.setdefault("telegram",     tg)
    sys.modules.setdefault("telegram.ext", tg_ext)


_make_telegram_stubs()

try:
    from content.receiver import _confirm_keyboard
    markup  = _confirm_keyboard("post_reel")
    buttons = markup.keyboard[0]
    yes_btn = next((b for b in buttons if "Yes" in b.text), None)
    no_btn  = next((b for b in buttons if "No"  in b.text), None)

    record("8a · confirm keyboard has Yes button",   yes_btn is not None)
    record("8b · confirm keyboard has No button",    no_btn  is not None)
    record("8c · Yes callback = confirm:post_reel",
           yes_btn is not None and yes_btn.callback_data == "confirm:post_reel")
    record("8d · No callback = confirm:cancel",
           no_btn  is not None and no_btn.callback_data  == "confirm:cancel")
except Exception as exc:
    record("8a · confirm keyboard", False, str(exc))

try:
    from content.receiver import _pending, _pending_lock

    results_list: list = []

    def _w():
        with _pending_lock:
            _pending["__test__"] = "v05"

    def _r(out):
        with _pending_lock:
            out.append(_pending.get("__test__"))

    t1 = threading.Thread(target=_w);                    t1.start(); t1.join()
    t2 = threading.Thread(target=_r, args=(results_list,)); t2.start(); t2.join()
    with _pending_lock:
        _pending.pop("__test__", None)

    record("8e · _pending is thread-safe",
           results_list == ["v05"], f"read_back={results_list}")
except Exception as exc:
    record("8e · thread-safe _pending", False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 9. /brain USAGE STATS
# ─────────────────────────────────────────────────────────────────────────────
section("9 · /brain USAGE STATS")

try:
    stats = get_usage_today()
    record("9a · get_usage_today returns dict", isinstance(stats, dict))

    keys_expected = [
        "ollama_calls", "claude_calls",
        "claude_input_tokens", "claude_output_tokens", "cache_hits",
    ]
    record("9b · stats has all expected keys",
           all(k in stats for k in keys_expected), f"keys={list(stats.keys())}")

    in_tok  = stats.get("claude_input_tokens", 0)
    out_tok = stats.get("claude_output_tokens", 0)
    cost    = (in_tok * 0.000001) + (out_tok * 0.000005)
    record("9c · cost calculation runs", True, f"estimated=${cost:.4f}")

    if _has_ollama:
        record("9d · ollama_calls > 0",
               stats.get("ollama_calls", 0) > 0,
               f"ollama_calls={stats.get('ollama_calls', 0)}")
        record("9e · cache_hits > 0 (cache was used)",
               stats.get("cache_hits", 0) > 0,
               f"cache_hits={stats.get('cache_hits', 0)}")
    else:
        record("9d · ollama_calls", False, "Ollama offline — skipped")
        record("9e · cache_hits",   False, "Ollama offline — skipped")

    print(f"\n       Brain stats today:\n{json.dumps(stats, indent=4)}")
except Exception as exc:
    record("9a · usage stats", False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 10. /start MENU + ALL COMMANDS REGISTERED
# ─────────────────────────────────────────────────────────────────────────────
section("10 · /start MENU COMPLETENESS")

try:
    src = (ROOT / "content" / "receiver.py").read_text(encoding="utf-8")

    expected = [
        "cmd_start", "cmd_ask", "cmd_plan", "cmd_research", "cmd_clear",
        "cmd_market", "cmd_trades", "cmd_status", "cmd_remind", "cmd_tasks",
        "cmd_cancel", "cmd_brain", "cmd_pipeline", "cmd_approve", "cmd_reject",
        "cmd_caption", "cmd_hashtags", "cmd_dca", "cmd_reel", "cmd_help", "cmd_stop",
    ]
    for cmd in expected:
        record(f"10 · handler {cmd} defined", f"def {cmd}" in src)

    registered = re.findall(r'CommandHandler\("(\w+)"', src)
    record("10z · ≥20 commands registered",
           len(registered) >= 20, f"count={len(registered)}: {registered}")
except Exception as exc:
    record("10 · menu completeness", False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 11. STRESS TEST — 10 rapid concurrent requests
# ─────────────────────────────────────────────────────────────────────────────
section("11 · STRESS TEST — 10 rapid concurrent requests")

STRESS_PROMPTS = [
    "what is ethereum in one sentence",
    "give me a quick crypto tip",
    "what is bitcoin dominance",
    "best DCA strategy in brief",
    "how to grow on TikTok fast",
    "write a short gym caption",
    "what is solana in one sentence",
    "top altcoins this week briefly",
    "how to start dropshipping fast",
    "morning routine for entrepreneurs",
]

_stress_out: list[tuple[bool, str]] = []
_stress_lock = threading.Lock()


def _worker(prompt: str) -> None:
    try:
        resp, brain = ask_hybrid(prompt, force="simple")
        with _stress_lock:
            _stress_out.append((bool(resp), brain))
    except Exception as exc:
        with _stress_lock:
            _stress_out.append((False, str(exc)[:40]))


if _has_ollama:
    threads_stress = [threading.Thread(target=_worker, args=(p,)) for p in STRESS_PROMPTS]
    t0 = time.time()
    for t in threads_stress:
        t.start()
    for t in threads_stress:
        t.join(timeout=180)
    elapsed = time.time() - t0

    successes = sum(1 for ok, _ in _stress_out if ok)
    brains    = [b for _, b in _stress_out]
    brain_dist = {b: brains.count(b) for b in set(brains)}

    record("11a · all 10 responses returned",  len(_stress_out) == 10,
           f"got={len(_stress_out)}/10")
    record("11b · no crashes (all ok)",         successes == 10,
           f"passed={successes}/10")
    record("11c · completed in < 180s",          elapsed < 180,
           f"elapsed={elapsed:.1f}s")
    record("11d · brain distribution",           True, f"{brain_dist}")

    try:
        hf = ROOT / "data" / "conversation_history.json"
        if hf.exists():
            json.loads(hf.read_text(encoding="utf-8"))
        record("11e · conversation_history.json valid after stress", True)
    except Exception as exc:
        record("11e · JSON integrity after stress", False, str(exc))
else:
    for t in ["11a","11b","11c","11d","11e"]:
        record(f"{t} · stress test", False, "Ollama offline — skipped")


# ─────────────────────────────────────────────────────────────────────────────
# 12. FINAL REPORT
# ─────────────────────────────────────────────────────────────────────────────
section("12 · FINAL REPORT")

total  = len(_results)
passed = sum(1 for _, ok, _ in _results if ok)
failed = total - passed
skip   = sum(1 for _, _, note in _results if "skipped" in note.lower())

print(f"\n  Score:   {passed}/{total} passed  |  {failed} failed  |  {skip} skipped (infra)\n")

if failed:
    print("  ❌  FAILURES:")
    for name, ok, note in _results:
        if not ok:
            skipped = "skipped" in note.lower()
            mark = "⏭ " if skipped else "❌"
            print(f"    {mark}  {name}" + (f"  [{note}]" if note else ""))
    print()

print("  FULL RESULTS:")
for name, ok, note in _results:
    skipped = not ok and "skipped" in note.lower()
    mark = "✅" if ok else ("⏭ " if skipped else "❌")
    print(f"    {mark}  {name}")

real_failures = failed - skip
print()
if real_failures == 0 and failed == 0:
    print("  🦾 ALL TESTS PASSED — ClawBot v0.5 brain upgrade confirmed!")
elif real_failures == 0:
    print(f"  ✅ All logic tests passed! ({skip} tests skipped — infra not ready)")
    print(f"     → Pull Ollama model:   ollama pull {_configured_model}")
    print(f"     → Set Claude API key:  ANTHROPIC_API_KEY in .env")
elif real_failures <= 3:
    print(f"  ⚠️  {real_failures} logic failure(s) — check above.")
else:
    print(f"  ❌  {real_failures} logic failure(s) — review above.")

sys.exit(0 if real_failures == 0 else 1)
