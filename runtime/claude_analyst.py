"""Claude Opus strategy analyst — reads trade outcomes and generates recommendations.

Position in the architecture:
    Claude Opus (this module)
        ↓ reads trade_outcomes.jsonl + live market context
    AnalysisReport
        ↓ feeds strategy weight hints + Ruflo memory directives
    Ruflo Multi-Agent Swarm (runtime/ruflo_agent.py)
        ↓
    Trade Intent Layer → Risk Kernel → Crypto.com Exchange

Called:
  - Automatically at end of each backtest (backtest_day.py)
  - Daily by CryptoComBot.flush_daily_summary()
  - On demand: python -m runtime.claude_analyst [outcomes_file.jsonl]

Writes: data/optimization/analysis_<ts>.json
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.claude_analyst")

_OUTCOMES_DIR  = Path(__file__).parent.parent / "data" / "logs"
_ANALYSIS_DIR  = Path(__file__).parent.parent / "data" / "optimization"
_WEIGHTS_FILE  = Path(__file__).parent.parent / "data" / "blofin_weights.json"
_MODEL         = "claude-opus-4-7"
_MAX_TOKENS    = 4096

_ANALYSIS_PROMPT = """\
You are analyzing a crypto trading bot's recent trade history (running on Crypto.com \
futures) to find patterns and generate concrete, code-implementable improvements.

## Trade Outcomes — last {n} closed trades
Each record has: strategy, symbol, side, outcome (win/loss), pnl, entry_price, \
exit_price, regime (market regime at entry), signal_reason (what triggered the entry), \
and narrative (WHY it won or lost, pre-computed by the bot).

```json
{outcomes_json}
```

## Current Strategy Weights (1.0 = normal size, 2.0 = double, 0.2 = 20%)
```json
{weights_json}
```

## Aggregated Stats by Strategy
{strategy_summary}

## Task
Return ONLY a valid JSON object (no markdown fences, no prose outside the JSON):

{{
  "session_date": "{date}",
  "overall_health": "STRONG|MODERATE|WEAK",
  "win_rate_pct": <float>,
  "expectancy_usd": <float>,
  "strategies": {{
    "<STRATEGY_NAME>": {{
      "status": "PERFORMING|STRUGGLING|INVESTIGATE",
      "win_rate": <float 0-1>,
      "total_pnl": <float>,
      "trades": <int>,
      "key_finding": "<1 sentence — reference actual trade data>",
      "recommendation": "<specific, actionable code change>",
      "priority": "HIGH|MEDIUM|LOW"
    }}
  }},
  "regime_analysis": [
    {{
      "regime": "<name>",
      "win_rate": <float 0-1>,
      "issue": "<describe the problem — be specific>",
      "fix": "<concrete suggestion, e.g. block SHORT in TRENDING_BULL>"
    }}
  ],
  "top_failure_patterns": [
    "<pattern 1 — specific, reference regime/strategy/signal combos>",
    "<pattern 2>",
    "<pattern 3>"
  ],
  "top_win_patterns": [
    "<what setup consistently produced wins and why>"
  ],
  "immediate_actions": [
    "<#1 highest-priority code change to make right now>"
  ],
  "weight_adjustments": {{
    "<STRATEGY_NAME>": <suggested_weight_float>
  }},
  "ruflo_learning_directive": "<what pattern Ruflo HNSW memory should prioritize — 1 sentence>"
}}

Rules:
- Be specific. Name actual trades: 'BREAKOUT SHORT in TRENDING_BULL on 3 occasions'
- weight_adjustments: only include strategies that need changing
- If fewer than 5 trades exist, say overall_health=UNKNOWN and note insufficient data
- Recommendations must be implementable — reference parameter names, thresholds
"""


@dataclass
class StrategyInsight:
    status:         str
    win_rate:       float
    total_pnl:      float
    trades:         int
    key_finding:    str
    recommendation: str
    priority:       str


@dataclass
class AnalysisReport:
    session_date:           str
    overall_health:         str
    win_rate_pct:           float
    expectancy_usd:         float
    strategies:             Dict[str, StrategyInsight]
    regime_analysis:        List[Dict[str, Any]]
    top_failure_patterns:   List[str]
    top_win_patterns:       List[str]
    immediate_actions:      List[str]
    weight_adjustments:     Dict[str, float]
    ruflo_learning_directive: str
    raw_json:               Dict[str, Any] = field(default_factory=dict)
    model_used:             str = _MODEL
    outcomes_analyzed:      int = 0
    saved_to:               str = ""
    error:                  Optional[str] = None


class ClaudeAnalyst:
    """Calls Claude Opus to analyze trade outcomes and return strategy recommendations."""

    def __init__(self, model: str = _MODEL, max_tokens: int = _MAX_TOKENS):
        self._model      = model
        self._max_tokens = max_tokens
        self._client     = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                api_key = os.getenv("ANTHROPIC_API_KEY", "")
                self._client = (anthropic.Anthropic(api_key=api_key)
                                if api_key else anthropic.Anthropic())
            except ImportError:
                raise RuntimeError("anthropic package not installed — run: pip install anthropic")
        return self._client

    def analyze(
        self,
        outcomes_file:   Optional[Path] = None,
        max_outcomes:    int = 100,
        extra_context:   Optional[str] = None,
    ) -> AnalysisReport:
        """Load trade outcomes, call Claude Opus, return AnalysisReport.

        Args:
            outcomes_file: specific .jsonl to read; None = auto-select latest
            max_outcomes:  cap on records sent to Claude (cost control)
            extra_context: optional freeform text appended to prompt (e.g. live MCP data)
        """
        if outcomes_file is None:
            outcomes_file = self._find_latest_outcomes()
        if outcomes_file is None or not outcomes_file.exists():
            return self._empty_report("No trade outcomes file found — run a backtest first")

        records = self._load_records(outcomes_file, max_outcomes)
        if not records:
            return self._empty_report("Outcomes file is empty")

        prompt = _ANALYSIS_PROMPT.format(
            n=len(records),
            outcomes_json=json.dumps(records, indent=2),
            weights_json=self._load_weights(),
            strategy_summary=self._strategy_summary(records),
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        if extra_context:
            prompt += f"\n\n## Additional Market Context (from Crypto.com live data)\n{extra_context}"

        try:
            client   = self._get_client()
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=(
                    "You are a quantitative trading analyst specializing in crypto futures. "
                    "You return only valid JSON. You are precise, specific, and honest about "
                    "what the data shows — never pad with generic advice."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text.strip()
        except Exception as exc:
            logger.error("Claude Opus API call failed: %s", exc)
            return self._empty_report(f"API error: {exc}")

        data = self._parse_json(raw_text)
        if data is None:
            return self._empty_report("Claude returned unparseable response")

        saved_path = self._save_report(data, outcomes_file.name)
        return self._build_report(data, len(records), saved_path)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_latest_outcomes(self) -> Optional[Path]:
        files = sorted(
            _OUTCOMES_DIR.glob("trade_outcomes*.jsonl"),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        return files[0] if files else None

    def _load_records(self, path: Path, limit: int) -> List[Dict]:
        records = []
        for ln in path.read_text().splitlines():
            try:
                records.append(json.loads(ln))
            except Exception:
                pass
        return records[-limit:]

    def _strategy_summary(self, records: List[Dict]) -> str:
        stats: Dict[str, Dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
        for r in records:
            name = r.get("strategy", "UNKNOWN")
            if r.get("outcome") == "win":
                stats[name]["wins"] += 1
            else:
                stats[name]["losses"] += 1
            stats[name]["pnl"] += r.get("pnl", 0.0)
        lines = []
        for name, st in sorted(stats.items()):
            total = st["wins"] + st["losses"]
            wr    = st["wins"] / total * 100 if total else 0.0
            lines.append(f"  {name:<20} {total:>5} trades  {wr:>5.0f}% WR  PnL={st['pnl']:+.4f}")
        return "\n".join(lines) if lines else "  No data yet"

    def _load_weights(self) -> str:
        try:
            return _WEIGHTS_FILE.read_text()
        except Exception:
            return "{}"

    def _parse_json(self, text: str) -> Optional[Dict]:
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            start = text.index("{")
            end   = text.rindex("}") + 1
            return json.loads(text[start:end])
        except Exception as exc:
            logger.error("JSON parse failed: %s\nRaw: %.200s", exc, text)
            return None

    def _save_report(self, data: Dict, source_file: str) -> str:
        _ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = _ANALYSIS_DIR / f"analysis_{ts}.json"
        data["_meta"] = {"ts": ts, "source": source_file, "model": self._model}
        path.write_text(json.dumps(data, indent=2))
        logger.info("Analysis saved → %s", path)
        return str(path)

    def _build_report(self, data: Dict, n: int, saved_path: str) -> AnalysisReport:
        strategies: Dict[str, StrategyInsight] = {}
        for name, si in data.get("strategies", {}).items():
            strategies[name] = StrategyInsight(
                status=si.get("status", "UNKNOWN"),
                win_rate=float(si.get("win_rate", 0)),
                total_pnl=float(si.get("total_pnl", 0)),
                trades=int(si.get("trades", 0)),
                key_finding=si.get("key_finding", ""),
                recommendation=si.get("recommendation", ""),
                priority=si.get("priority", "MEDIUM"),
            )
        return AnalysisReport(
            session_date=data.get("session_date", ""),
            overall_health=data.get("overall_health", "UNKNOWN"),
            win_rate_pct=float(data.get("win_rate_pct", 0)),
            expectancy_usd=float(data.get("expectancy_usd", 0)),
            strategies=strategies,
            regime_analysis=data.get("regime_analysis", []),
            top_failure_patterns=data.get("top_failure_patterns", []),
            top_win_patterns=data.get("top_win_patterns", []),
            immediate_actions=data.get("immediate_actions", []),
            weight_adjustments=data.get("weight_adjustments", {}),
            ruflo_learning_directive=data.get("ruflo_learning_directive", ""),
            raw_json=data,
            model_used=self._model,
            outcomes_analyzed=n,
            saved_to=saved_path,
        )

    def _empty_report(self, error: str) -> AnalysisReport:
        return AnalysisReport(
            session_date=datetime.now(timezone.utc).isoformat(),
            overall_health="UNKNOWN", win_rate_pct=0.0, expectancy_usd=0.0,
            strategies={}, regime_analysis=[], top_failure_patterns=[],
            top_win_patterns=[], immediate_actions=[], weight_adjustments={},
            ruflo_learning_directive="", error=error,
        )


# ── Display ───────────────────────────────────────────────────────────────────

def print_report(report: AnalysisReport) -> None:
    """Pretty-print an AnalysisReport to stdout."""
    ICONS = {"STRONG": "[OK]", "MODERATE": "[~~]", "WEAK": "[!!]", "UNKNOWN": "[??]"}
    SICONS = {"PERFORMING": "[OK]", "STRUGGLING": "[~~]", "INVESTIGATE": "[!!]"}

    print("\n" + "=" * 70)
    print(f"  CLAUDE OPUS ANALYSIS  —  {report.session_date or 'now'}")
    print(f"  Model: {report.model_used}  |  Outcomes analyzed: {report.outcomes_analyzed}")
    print("=" * 70)

    if report.error:
        print(f"\n  NOTE: {report.error}")
        return

    icon = ICONS.get(report.overall_health, "[?]")
    print(f"\n  Overall Health   : {icon} {report.overall_health}")
    print(f"  Win Rate         : {report.win_rate_pct:.1f}%")
    print(f"  Expectancy/trade : {report.expectancy_usd:+.4f} USDT")

    if report.strategies:
        print("\n  Strategy Health")
        print("  " + "-" * 66)
        for name, si in report.strategies.items():
            sicon = SICONS.get(si.status, "[?]")
            wr_str = f"{si.win_rate * 100:.0f}%"
            print(f"  {sicon} {si.status:<12} {name:<20}  WR={wr_str:<5}  PnL={si.total_pnl:+.4f}")
            print(f"      Finding : {si.key_finding}")
            print(f"      Action  : {si.recommendation}  [{si.priority}]")

    if report.weight_adjustments:
        print("\n  Suggested Weight Adjustments")
        for name, w in report.weight_adjustments.items():
            print(f"    {name:<20} → {w:.2f}x")

    if report.top_failure_patterns:
        print("\n  Failure Patterns")
        for p in report.top_failure_patterns:
            print(f"    - {p}")

    if report.top_win_patterns:
        print("\n  Win Patterns")
        for p in report.top_win_patterns:
            print(f"    + {p}")

    if report.regime_analysis:
        print("\n  Regime Analysis")
        for r in report.regime_analysis:
            wr_str = f"{r.get('win_rate', 0) * 100:.0f}%"
            print(f"    {r.get('regime', '?'):<25}  WR={wr_str:<5}  {r.get('issue', '')}")
            if r.get("fix"):
                print(f"      Fix: {r['fix']}")

    if report.immediate_actions:
        print("\n  Immediate Actions")
        for i, a in enumerate(report.immediate_actions, 1):
            print(f"    {i}. {a}")

    if report.ruflo_learning_directive:
        print(f"\n  Ruflo Directive: {report.ruflo_learning_directive}")

    if report.saved_to:
        print(f"\n  Full report: {Path(report.saved_to).name}")

    print("=" * 70)


# ── Entry point ───────────────────────────────────────────────────────────────

def run_analysis(
    outcomes_file: Optional[Path] = None,
    extra_context: Optional[str] = None,
    silent: bool = False,
) -> AnalysisReport:
    """Analyze outcomes and optionally print the report. Returns the report."""
    analyst = ClaudeAnalyst()
    report  = analyst.analyze(outcomes_file=outcomes_file, extra_context=extra_context)
    if not silent:
        print_report(report)
    return report


if __name__ == "__main__":
    import sys
    f = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    run_analysis(f)
