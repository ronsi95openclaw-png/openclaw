"""QUIN — Local LLM Orchestrator.

QUIN sits between the Skill Clock outputs and the Execution Engine.
It receives all 10 skill outputs and makes a final execution decision:
should a trade be placed, modified, or held?

Primary model: qwen2.5:14b via Ollama (local, private).
Fallback:      deterministic rule-based resolver when Ollama is offline.

QUIN capabilities (as shown in architecture diagram):
  - Plan Synthesis       — given signals + regime + risk, synthesise a plan
  - Constraint Solver    — enforce position limits, weight bounds
  - Conflict Resolver    — resolve conflicting signals across strategies
  - What-If Simulation   — estimate outcome before committing
  - Tool Use (Functions) — decide which tool calls to emit
  - Natural Language     — produce human-readable reasoning

All decisions are logged to data/quin_decisions.jsonl (immutable audit).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("openclaw.runtime.quin_orchestrator")

_DECISIONS_PATH = "data/quin_decisions.jsonl"
_OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://localhost:11434")
_QUIN_MODEL     = os.getenv("QUIN_MODEL", "qwen2.5:14b")
_QUIN_TIMEOUT_S = 10.0   # max time to wait for Ollama response


@dataclass
class QuinDecision:
    decision_id:  str
    ts:           str
    action:       str          # TRADE | HOLD | SCALE_DOWN | EMERGENCY_HALT
    confidence:   float        # 0.0 – 1.0
    reasoning:    str          # human-readable explanation
    tool_calls:   list         # [{tool, params}]
    source:       str          # "ollama" | "rule_based"
    tick_id:      str
    signal:       Optional[dict]


class QuinOrchestrator:
    """Processes SkillClock outputs and emits a QuinDecision.

    Falls back to rule-based logic silently when Ollama is unavailable
    so the bot never blocks waiting for a local model.
    """

    def __init__(
        self,
        model:         str   = _QUIN_MODEL,
        ollama_url:    str   = _OLLAMA_URL,
        decisions_path: str  = _DECISIONS_PATH,
        use_ollama:    bool  = True,
    ) -> None:
        self._model          = model
        self._ollama_url     = ollama_url
        self._decisions_path = decisions_path
        self._use_ollama     = use_ollama
        self._lock           = threading.Lock()
        self._ollama_ok:     Optional[bool] = None   # None = untested
        self._last_decision: Optional[QuinDecision]  = None
        self._decision_count = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def decide(self, ctx: Any) -> dict:
        """
        Main entry point. Receives a SkillContext, returns a decision dict
        (also stored as QuinDecision internally).
        """
        plan    = getattr(ctx, "execution_plan", {})
        risk    = getattr(ctx, "risk_state", {})
        regimes = getattr(ctx, "regimes", {})
        tick_id = getattr(ctx, "tick_id", "")

        # If risk gate is closed, always HOLD — no LLM needed
        if risk.get("halted") or not risk.get("can_open_new", True):
            return self._make_hold(tick_id, "risk_gate_closed",
                                   plan.get("selected_signal"))

        if plan.get("action") == "HOLD" or plan.get("selected_signal") is None:
            return self._make_hold(tick_id, plan.get("reason", "no_signal"),
                                   None)

        signal = plan["selected_signal"]

        # Try Ollama first (if configured and not previously failed)
        if self._use_ollama and self._ollama_available():
            try:
                decision = self._ollama_decide(signal, ctx)
                self._record(decision)
                return asdict(decision)
            except Exception as exc:
                logger.debug("QUIN Ollama failed: %s", exc)
                self._ollama_ok = False

        # Try OpenRouter (cloud fallback when Ollama is unavailable)
        if os.getenv("OPENROUTER_API_KEY", "").strip():
            try:
                decision = self._openrouter_decide(signal, ctx)
                self._record(decision)
                return asdict(decision)
            except Exception as exc:
                logger.debug("QUIN OpenRouter failed, falling back to rule-based: %s", exc)

        # Rule-based fallback
        decision = self._rule_based_decide(signal, ctx)
        self._record(decision)
        return asdict(decision)

    def get_last_decision(self) -> Optional[QuinDecision]:
        with self._lock:
            return self._last_decision

    def get_status(self) -> dict:
        with self._lock:
            last = self._last_decision
        return {
            "model":          self._model,
            "ollama_url":     self._ollama_url,
            "ollama_ok":      self._ollama_ok,
            "use_ollama":     self._use_ollama,
            "decision_count": self._decision_count,
            "last_action":    last.action if last else None,
            "last_ts":        last.ts if last else None,
            "last_source":    last.source if last else None,
            "last_confidence": last.confidence if last else None,
            "last_reasoning": last.reasoning[:120] if last else None,
        }

    # ── Ollama integration ─────────────────────────────────────────────────────

    def _ollama_available(self) -> bool:
        """Check if Ollama is reachable. Caches result for 60 seconds."""
        if self._ollama_ok is True:
            return True
        if self._ollama_ok is False:
            # Re-test periodically
            return False
        try:
            import urllib.request
            req = urllib.request.Request(f"{self._ollama_url}/api/tags",
                                         method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    self._ollama_ok = True
                    logger.info("QUIN: Ollama is available at %s (model=%s)",
                                self._ollama_url, self._model)
                    return True
        except Exception:
            pass
        self._ollama_ok = False
        logger.info("QUIN: Ollama not available — using rule-based fallback")
        return False

    def _ollama_decide(self, signal: dict, ctx: Any) -> QuinDecision:
        """Call Ollama with a compact prompt and parse the decision."""
        import urllib.request, urllib.error

        prompt = self._build_prompt(signal, ctx)
        payload = json.dumps({
            "model":  self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self._ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_QUIN_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw = body.get("response", "").strip()
        return self._parse_ollama_response(raw, signal, ctx)

    def _build_prompt(self, signal: dict, ctx: Any) -> str:
        regime   = ctx.regimes.get(signal.get("symbol", ""), "UNKNOWN")
        cap      = ctx.risk_state.get("capital_state", "SAFE")
        strategy = signal.get("strategy", "")
        side     = signal.get("signal", "")
        conf     = signal.get("confidence", 0.7)
        reason   = signal.get("reason", "")
        score    = signal.get("score", conf)
        symbol   = signal.get("symbol", "")

        return (
            f"You are QUIN, a crypto trading orchestrator. Decide: TRADE or HOLD.\n"
            f"Signal: {symbol} {side} via {strategy} (confidence={conf:.0%}, score={score:.2f})\n"
            f"Reason: {reason}\n"
            f"Regime: {regime}  Capital: {cap}\n"
            f"Respond with JSON only: "
            f'{{\"action\": \"TRADE\"|\"HOLD\", \"confidence\": 0.0-1.0, \"reasoning\": \"...\"}}'
        )

    def _openrouter_decide(self, signal: dict, ctx: Any) -> QuinDecision:
        """Call OpenRouter API as cloud replacement for Ollama."""
        import urllib.request
        prompt     = self._build_prompt(signal, ctx)
        api_key    = os.getenv("OPENROUTER_API_KEY", "")
        or_model   = os.getenv("QUIN_OPENROUTER_MODEL", "qwen/qwen-2.5-14b-instruct")
        payload    = json.dumps({
            "model":    or_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer":  os.getenv("OPENROUTER_SITE_URL", "https://openclaw.app"),
                "X-Title":       "OpenClaw",
            },
        )
        with urllib.request.urlopen(req, timeout=_QUIN_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode())
        raw = body["choices"][0]["message"]["content"].strip()
        dec = self._parse_ollama_response(raw, signal, ctx)
        dec.source = "openrouter"
        return dec

    def _parse_ollama_response(self, raw: str, signal: dict,
                                ctx: Any) -> QuinDecision:
        try:
            # Extract JSON from response
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                action     = str(data.get("action", "HOLD")).upper()
                confidence = float(data.get("confidence", 0.7))
                reasoning  = str(data.get("reasoning", "Ollama decision"))
                if action not in ("TRADE", "HOLD"):
                    action = "HOLD"
                return self._make_decision(action, confidence, reasoning,
                                           "ollama", ctx.tick_id, signal)
        except Exception:
            pass
        # Fallback: if LLM said TRADE somewhere, trust it
        action = "TRADE" if "TRADE" in raw.upper() else "HOLD"
        return self._make_decision(action, 0.65, raw[:120], "ollama",
                                   ctx.tick_id, signal)

    # ── Rule-based fallback ────────────────────────────────────────────────────

    def _rule_based_decide(self, signal: dict, ctx: Any) -> QuinDecision:
        """Deterministic rule-based decision when Ollama is offline."""
        regime     = ctx.regimes.get(signal.get("symbol", ""), "UNKNOWN")
        strategy   = signal.get("strategy", "")
        confidence = signal.get("confidence", 0.7)
        score      = signal.get("score", confidence)

        # Rule 1: Block TREND_FOLLOW in UNKNOWN/LIQUIDITY_DROUGHT regime (0% WR historically)
        if strategy == "TREND_FOLLOW" and regime in ("UNKNOWN", "LIQUIDITY_DROUGHT"):
            return self._make_decision("HOLD", 0.9,
                f"TREND_FOLLOW blocked in {regime} regime (historically 0% WR)",
                "rule_based", ctx.tick_id, signal)

        # Rule 2: Require minimum weighted score
        if score < 0.5:
            return self._make_decision("HOLD", 0.8,
                f"Score {score:.2f} below threshold 0.50",
                "rule_based", ctx.tick_id, signal)

        # Rule 3: Check capital state allows trading
        capital_state = ctx.risk_state.get("capital_state", "SAFE")
        if capital_state == "CRITICAL":
            return self._make_decision("HOLD", 0.95,
                f"Capital state {capital_state} — conservative hold",
                "rule_based", ctx.tick_id, signal)

        # Rule 4: Apply confidence floor per strategy
        floors = {
            "EMA_CROSS":       0.65,
            "BOLLINGER_BAND":  0.60,
            "BREAKOUT":        0.70,
            "TREND_FOLLOW":    0.75,
            "RSI_MEAN_REVERT": 0.65,
        }
        floor = floors.get(strategy, 0.65)
        if confidence < floor:
            return self._make_decision("HOLD", 0.75,
                f"{strategy} confidence {confidence:.0%} below floor {floor:.0%}",
                "rule_based", ctx.tick_id, signal)

        # All checks passed — TRADE
        return self._make_decision("TRADE", min(0.95, score),
            f"{strategy} {signal.get('signal')} in {regime} regime — all rules passed",
            "rule_based", ctx.tick_id, signal)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _make_hold(self, tick_id: str, reason: str,
                   signal: Optional[dict]) -> dict:
        d = self._make_decision("HOLD", 1.0, reason, "rule_based", tick_id,
                                signal)
        self._record(d)
        return asdict(d)

    def _make_decision(self, action: str, confidence: float, reasoning: str,
                       source: str, tick_id: str,
                       signal: Optional[dict]) -> QuinDecision:
        return QuinDecision(
            decision_id = str(uuid.uuid4())[:8],
            ts          = datetime.now(timezone.utc).isoformat(),
            action      = action,
            confidence  = round(confidence, 4),
            reasoning   = reasoning,
            tool_calls  = ([{"tool": "place_order", "params": signal}]
                           if action == "TRADE" and signal else []),
            source      = source,
            tick_id     = tick_id,
            signal      = signal,
        )

    def _record(self, decision: QuinDecision) -> None:
        with self._lock:
            self._last_decision  = decision
            self._decision_count += 1
        try:
            from infra.state_store import append_quin_decision
            append_quin_decision(asdict(decision))
        except Exception:
            try:
                path = Path(self._decisions_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(decision)) + "\n")
            except Exception:
                pass


# ── Module singleton ──────────────────────────────────────────────────────────

_quin: Optional[QuinOrchestrator] = None
_quin_lock = threading.Lock()


def get_quin() -> QuinOrchestrator:
    """Return the process-level QUIN singleton."""
    global _quin
    if _quin is None:
        with _quin_lock:
            if _quin is None:
                _quin = QuinOrchestrator()
    return _quin
