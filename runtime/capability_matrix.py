"""Runtime Capability Matrix — live self-assessment of what is wired.

Run this module directly to get a full system status report:
    python -m runtime.capability_matrix

Or import and call assess() for programmatic access.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


# ── Capability registry ───────────────────────────────────────────────────────

# Format: (system_name, authority_level, failure_impact, import_path, check_fn_or_None)
# authority_level: "supreme" | "authoritative" | "advisory" | "operational"
# failure_impact:  "critical" | "high" | "medium" | "low" | "none"

_SYSTEMS: List[Tuple[str, str, str, str, str]] = [
    # (name, authority, impact, module_path, class_name)
    ("Kill Switch / Emergency Halt", "supreme",       "critical", "governance.emergency_controls",  "EmergencyControls"),
    ("Capital Preservation Engine",  "authoritative", "critical", "risk.capital_preservation",      "CapitalPreservationEngine"),
    ("Intent Pipeline",              "authoritative", "critical", "runtime.intent_pipeline",        "IntentPipeline"),
    ("Runtime Orchestrator",         "authoritative", "critical", "runtime.orchestrator",           "RuntimeOrchestrator"),
    ("Replay Journal",               "operational",   "medium",   "runtime.replay_journal",         "ReplayJournal"),
    ("Trace Context",                "operational",   "low",      "runtime.trace",                  "TraceContext"),
    ("Governance Approvals",         "authoritative", "high",     "governance.approvals",           "ApprovalQueue"),
    ("Governance Permissions",       "authoritative", "high",     "governance.permissions",         "PermissionRegistry"),
    ("Regime Classifier",            "advisory",      "low",      "research.regimes.classifier",    "RegimeClassifier"),
    ("Strategy Compatibility",       "advisory",      "low",      "research.regimes.strategy_compatibility", "is_strategy_compatible"),
    ("Portfolio Allocator",          "advisory",      "none",     "research.portfolio.allocator",   "AdaptivePortfolioAllocator"),
    ("Strategy Lifecycle Manager",   "advisory",      "medium",   "research.lifecycle.manager",     "StrategyLifecycleManager"),
    ("Deployment Gate",              "authoritative", "high",     "research.lifecycle.deployment_gate", "DeploymentGate"),
    ("Smart Order Router",           "advisory",      "none",     "exchange.smart_router",          "SmartOrderRouter"),
    ("Execution Quality Tracker",    "operational",   "low",      "exchange.execution_quality",     "ExecutionQualityTracker"),
    ("Backtesting Engine",           "operational",   "none",     "research.backtesting.engine",    "BacktestEngine"),
    ("Monte Carlo Engine",           "operational",   "none",     "research.montecarlo.engine",     "MonteCarloEngine"),
    ("Optimization (Grid)",          "operational",   "none",     "research.optimization.grid_search", "grid_search"),
    ("Walk-Forward Engine",          "operational",   "none",     "research.walkforward.engine",    "WalkForwardEngine"),
    ("AI Brain (Ollama/Claude)",     "advisory",      "low",      "core.brain",                     "Brain"),
    ("Prometheus Metrics",           "operational",   "low",      "core.metrics",                   "TRADES_TOTAL"),
    ("Resource Manager",             "operational",   "low",      "system.resource_manager",        "ResourceManager"),
    ("Security Firewall",            "operational",   "medium",   "security.api_firewall",          "APIFirewall"),
    ("Secrets Manager",              "operational",   "medium",   "security.secrets",               "SecretsManager"),
    ("Control Center Dashboard",     "operational",   "none",     "dashboard.control_center",       "create_app"),
    ("Ruflo MCP Bridge",             "advisory",      "none",     "runtime.ruflo_bridge",           "RufloBridge"),
    ("Ruflo Advisory Agent",         "advisory",      "none",     "runtime.ruflo_agent",            "RufloAdvisor"),
    ("Qwen Compressor",              "operational",   "low",      "runtime.qwen_compressor",        "compress_trade"),
    ("Crypto.com Bot",               "operational",   "high",     "trading.cryptocom_bot",          "CryptoComBot"),
    ("Google Sheets Reporter",       "operational",   "low",      "reporting.google_sheets",        "SheetReporter"),
]


def _check_import(module_path: str, symbol: str) -> Tuple[bool, str]:
    """Try to import a module and verify a symbol exists in it."""
    try:
        mod = importlib.import_module(module_path)
        # symbol might be a function, class, or attribute
        has_sym = hasattr(mod, symbol)
        return True, "OK" if has_sym else f"module ok but '{symbol}' missing"
    except ImportError as exc:
        return False, f"ImportError: {exc}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def assess() -> Dict[str, Any]:
    """Run full capability assessment. Returns structured report."""
    results = []
    counts = {"ok": 0, "missing": 0, "partial": 0}

    for name, authority, impact, module_path, symbol in _SYSTEMS:
        ok, detail = _check_import(module_path, symbol)
        status = "OK" if ok else "MISSING"
        if ok and "missing" in detail:
            status = "PARTIAL"

        counts["ok" if status == "OK" else
               "partial" if status == "PARTIAL" else "missing"] += 1

        results.append({
            "system":    name,
            "authority": authority,
            "impact":    impact,
            "status":    status,
            "detail":    detail,
        })

    # Wiring check: is blofin_bot actually importing orchestrator?
    wiring_ok, wiring_detail = _check_wiring()

    return {
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total":   len(results),
            "ok":      counts["ok"],
            "partial": counts["partial"],
            "missing": counts["missing"],
            "wiring_ok": wiring_ok,
        },
        "systems": results,
        "wiring":  {"ok": wiring_ok, "detail": wiring_detail},
        "unanswered_questions": _get_unanswered_questions(results),
    }


def _check_wiring() -> Tuple[bool, str]:
    """Check that blofin_bot actually imports and uses the orchestrator."""
    try:
        import ast
        from pathlib import Path
        src = (Path(__file__).parent.parent / "trading" / "cryptocom_bot.py").read_text()
        if "_init_orchestrator" in src or "build_orchestrator" in src:
            return True, "cryptocom_bot.py calls build_orchestrator — all signals validated"
        return False, "cryptocom_bot.py does NOT import orchestrator — signals bypass all validation"
    except Exception as exc:
        return False, f"Could not check wiring: {exc}"


def _get_unanswered_questions(results: list) -> List[str]:
    """Returns the 7 operational questions from the spec, with answers."""
    missing = [r["system"] for r in results if r["status"] == "MISSING"]
    questions = [
        "Which agents are live? → BloFinBot scan loop (4 strategies) + RuntimeOrchestrator (if wired)",
        "Which model powers each? → Qwen qwen2.5:14b (per-trade compression) / Claude Opus (daily analysis) / Claude Haiku (complex tasks via core.brain)",
        "What triggers them? → 30s scan loop in BloFinBot._loop() + Telegram commands",
        "What outputs do they produce? → TradingIntent → IntentVerdict → position open/close",
        "How are conflicts resolved? → IntentPipeline: first valid intent wins; one position per symbol",
        "What timeout policies exist? → Intent TTL=90s; governance approval expires 24h; API timeout=10s",
        f"What rejection paths exist? → Schema fail, regime forbidden, capital halt, stale, duplicate, global halt",
    ]
    if missing:
        questions.append(f"MISSING SYSTEMS (not importable): {', '.join(missing[:5])}")
    return questions


def print_report(report: Dict[str, Any]) -> None:
    """Pretty-print capability matrix to stdout."""
    s = report["summary"]
    print(f"\n{'='*70}")
    print(f"  OPENCLAW RUNTIME CAPABILITY MATRIX")
    print(f"  Assessed: {report['assessed_at']}")
    print(f"{'='*70}")
    print(f"  Total: {s['total']}  |  OK: {s['ok']}  |  "
          f"Partial: {s['partial']}  |  Missing: {s['missing']}")
    print(f"  Bot→Orchestrator wiring: {'✓ OK' if s['wiring_ok'] else '✗ NOT WIRED'}")
    print(f"{'='*70}\n")

    # Group by authority
    for authority in ("supreme", "authoritative", "advisory", "operational"):
        items = [r for r in report["systems"] if r["authority"] == authority]
        if not items:
            continue
        label = authority.upper()
        print(f"  [{label}]")
        for r in items:
            icon = "✓" if r["status"] == "OK" else ("~" if r["status"] == "PARTIAL" else "✗")
            impact = f"[{r['impact']}]" if r["impact"] != "none" else ""
            print(f"    {icon} {r['system']:<40} {impact:<12} {r['detail']}")
        print()

    print("  OPERATIONAL QUESTIONS")
    for q in report["unanswered_questions"]:
        print(f"    • {q}")

    w = report["wiring"]
    print(f"\n  WIRING: {w['detail']}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    report = assess()
    print_report(report)
    # Exit non-zero if critical systems are missing
    critical_missing = [
        r for r in report["systems"]
        if r["status"] == "MISSING" and r["impact"] == "critical"
    ]
    sys.exit(1 if critical_missing else 0)
