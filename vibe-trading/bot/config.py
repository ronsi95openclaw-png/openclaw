#!/usr/bin/env python3
"""
config.py — Central operational config + safe defaults for the TJR/ICT Lucid 25K bot.
=====================================================================================

Locked-contract role (see vibe-trading/bot/ARCHITECTURE.md §11):

    @dataclass(frozen=True)
    class BotConfig: go_live=False, instrument='ES', poll_interval_sec=10,
                     strategy=StrategyConfig(), daily_gate_pct=0.80,
                     consecutive_loss_limit=3, eod_flatten_et=time(15, 55),
                     log_dir=<bot>/logs
    def load_config(path=None) -> BotConfig

HARD SAFETY INVARIANTS honoured here (ARCHITECTURE.md §0):

  * Paper / DRY_RUN by DEFAULT. A live TraderPost POST may happen ONLY if BOTH
    os.environ["HERMES_BOT_LIVE"] == "1" AND config.go_live is True. Both default off.
    This module exposes ``is_live_enabled(config)`` so every caller agrees on the gate,
    but it NEVER POSTs anything — it only reports the gate state.

  * NO secrets are ever stored in BotConfig or in this file. The TraderPost webhook URL
    and secret are read from os.environ ONLY, inside traderpost.py, at call time. This
    module merely exposes the ENV VAR *NAMES* (constants) so callers don't hardcode them.

  * NO Lucid mandate numbers are hardcoded. ``load_mandate()`` here is a thin convenience
    wrapper that reads vibe-trading/lucid_mandate.json at runtime (same pattern as
    backtest/tjr_backtest.py). All limits stay authoritative in the JSON. The bot's
    canonical mandate view lives in mandate.py (MandateView); this helper exists so a
    caller that only needs the raw rules dict can grab it without a circular import.

  * Kill switch path is resolved from the mandate (``mandate["kill_switch"]["file"]``)
    relative to vibe-trading/. Only the LITERAL file vibe-trading/KILL_SWITCH triggers a
    halt; the currently-present KILL_SWITCH_DISABLED is NOT a trigger.

This module is import-safe: it performs no network or order I/O and never raises on a
missing mandate file (it falls back to the literal KILL_SWITCH path and stays DRY_RUN).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Recognised config keys (whitelist) ──────────────────────────────────────────────
# load_config() reads an ARBITRARY external JSON file (untrusted input). Only these
# top-level keys are honoured; any other key is logged and IGNORED (never executed,
# never used to weaken a safety control). "strategy" is a nested dict handled separately.
RECOGNISED_CONFIG_KEYS: frozenset = frozenset({
    "go_live",
    "instrument",
    "poll_interval_sec",
    "daily_gate_pct",
    "consecutive_loss_limit",
    "eod_flatten_et",
    "strategy",
})

# Safety ceiling: the EOD-flatten safety control / no-overnight mandate (close_eod=true,
# overnight_holds=false) must NEVER be pushable past the close. Any configured flatten
# time later than this is rejected and coerced back to the safe default. 15:55 ET is the
# spec default; we hard-reject anything >= 16:00 ET (regular-session close).
EOD_FLATTEN_MAX: time = time(15, 55)              # latest allowed flatten time
EOD_FLATTEN_HARD_CEILING: time = time(16, 0)      # absolute ceiling (RTH close)
EOD_FLATTEN_DEFAULT: time = time(15, 55)          # safe default

# Sane ceiling on the circuit-breaker so a config file cannot silently disable it by
# setting an absurdly high consecutive-loss limit.
CONSECUTIVE_LOSS_CEILING: int = 10

# ── Path anchors (no hardcoded user paths; everything resolved from this file) ──────

BOT_DIR: Path = Path(__file__).resolve().parent              # vibe-trading/bot/
VIBE_DIR: Path = BOT_DIR.parent                              # vibe-trading/
MANDATE_FILE: Path = VIBE_DIR / "lucid_mandate.json"         # authoritative Lucid rules
STRATEGY_SPEC: Path = VIBE_DIR / "strategies" / "tjr_lucid_strategy.md"
LOG_DIR: Path = BOT_DIR / "logs"                             # ARCHITECTURE.md §8
SESSIONS_DIR: Path = LOG_DIR / "sessions"

# Default kill-switch path used ONLY if the mandate file can't be read. The mandate's
# own kill_switch.file ("./KILL_SWITCH") is the source of truth at runtime.
DEFAULT_KILL_SWITCH_FILE: Path = VIBE_DIR / "KILL_SWITCH"

# ── Environment variable NAMES (names only — NEVER the values) ──────────────────────
# The live gate (§0.1) and TraderPost credentials (§9.3). Read from os.environ at call
# time inside traderpost.py / the gate helper below. Never logged, never defaulted.

ENV_LIVE_FLAG: str = "HERMES_BOT_LIVE"               # must == "1" to arm live (env half)
ENV_LIVE_VALUE: str = "1"                            # the exact value that arms it
ENV_TRADERPOST_WEBHOOK_URL: str = "TRADERPOST_WEBHOOK_URL"   # secret URL — env only
ENV_TRADERPOST_SECRET: str = "TRADERPOST_SECRET"            # signing secret — env only

# Convenience tuple for callers that want to verify presence without referencing names.
TRADERPOST_ENV_VARS: Tuple[str, str] = (ENV_TRADERPOST_WEBHOOK_URL, ENV_TRADERPOST_SECRET)

# ── StrategyConfig (strategy-local tunables; NOT the mandate) ───────────────────────
# Mirrors ARCHITECTURE.md §3.4. Kill-zone bounds and fib/OTE/MSB params live here, not
# in the mandate. tp1_rr/tp2_rr match tjr_backtest.py (2.0 / 4.0).


@dataclass(frozen=True)
class StrategyConfig:
    """Pure-strategy tunables fed to strategy.generate_signal(). No mandate numbers."""

    kill_zones: Tuple[str, ...] = ("ny_open",)   # NY Open 08:30–11:00 ET (primary window)
    lookback: int = 20                            # swing-range / sweep reference bars
    sweep_bars: int = 3                           # recent bars examined for the sweep
    msb_bars: int = 5                             # 1M swing window for MSB confirmation
    ote_low: float = 0.618                        # OTE fib retrace lower bound
    ote_high: float = 0.79                        # OTE fib retrace upper bound
    tp1_rr: float = 2.0                           # TP1 == 2R  (matches tjr_backtest.py)
    tp2_rr: float = 4.0                           # TP2 == 4R  (matches tjr_backtest.py)
    default_contracts: int = 1                    # risk_guard clamps to mandate cap


# ── BotConfig (operational config — NOT secrets, NOT mandate limits) ────────────────


@dataclass(frozen=True)
class BotConfig:
    """
    Operational configuration for the runner.

    SAFETY: ``go_live`` is the *config half* of the live gate; ``HERMES_BOT_LIVE`` is the
    *env half*. A live order requires BOTH (see is_live_enabled). Both default OFF, so the
    bot is paper/DRY_RUN by default. No secrets are ever stored on this object — the
    TraderPost webhook/secret are read from os.environ inside traderpost.py only.

    Mandate limits (max_loss_limit, max_position_size, daily_trade_cap,
    consistency_rule_eval, instruments_allowed, overnight_holds, close_eod) are NOT
    duplicated here; risk_guard / mandate read them from lucid_mandate.json at runtime.
    """

    go_live: bool = False                       # config half of the live gate (default OFF)
    instrument: str = "ES"                      # primary instrument (must be on allowlist)
    poll_interval_sec: int = 10                 # runner loop cadence
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    # Operational risk tunables (defaults match the strategy spec; mandate stays authoritative
    # for the hard dollar/size numbers via MandateView in mandate.py).
    daily_gate_pct: float = 0.80                # soft gate at 80% of mandate max_loss_limit
    consecutive_loss_limit: int = 3             # circuit-breaker on consecutive losers
    eod_flatten_et: time = time(15, 55)         # flatten by 15:55 ET (spec §Exit / EOD)

    # Filesystem anchors (resolved, not hardcoded).
    log_dir: Path = field(default_factory=lambda: LOG_DIR)
    mandate_file: Path = field(default_factory=lambda: MANDATE_FILE)
    kill_switch_file: Path = field(default_factory=lambda: DEFAULT_KILL_SWITCH_FILE)

    def __post_init__(self) -> None:
        # frozen dataclass: validate without mutating safety-critical fields.
        if self.poll_interval_sec <= 0:
            raise ValueError("poll_interval_sec must be > 0")
        # Soft gate must sit strictly BELOW the hard max_loss_limit so the spec's
        # slippage buffer survives (tjr_lucid_strategy.md §148/§163). 1.0 would make the
        # soft gate equal the hard limit and erase the buffer — reject it.
        if not (0.0 < self.daily_gate_pct < 1.0):
            raise ValueError("daily_gate_pct must be in (0, 1.0) — must stay below the hard max-loss limit")
        if self.consecutive_loss_limit < 1:
            raise ValueError("consecutive_loss_limit must be >= 1")


# ── Live-gate helper (the ONE place every caller agrees on the gate) ────────────────


def env_live_armed() -> bool:
    """True iff the ENV half of the live gate is armed (HERMES_BOT_LIVE == '1')."""
    return os.environ.get(ENV_LIVE_FLAG) == ENV_LIVE_VALUE


def is_live_enabled(config: BotConfig) -> bool:
    """
    The single source of truth for the live gate (ARCHITECTURE.md §0.1 / §9.1).

    Returns True ONLY if BOTH halves are armed:
        os.environ["HERMES_BOT_LIVE"] == "1"   AND   config.go_live is True

    If either is missing/false => DRY_RUN. This helper performs NO I/O and POSTs nothing;
    traderpost.py still re-checks the gate itself as defence in depth before any POST.
    """
    return env_live_armed() and bool(config.go_live)


# ── Mandate convenience loader (raw rules; mandate.py owns the typed MandateView) ───


def load_mandate(path: Optional[Path] = None) -> dict:
    """
    Read lucid_mandate.json and return the parsed mandate dict.

    Same pattern as backtest/tjr_backtest.py::load_mandate — read the JSON at runtime so
    edits take effect without a code change, and NEVER hardcode the Lucid numbers. The
    full object is returned (top-level ``rules``, ``kill_switch``, ``mode``); callers that
    only want the limits use ``result["rules"]``.

    On a missing/invalid file this returns a minimal, SAFE fallback that:
      * carries no real numeric limits beyond the structural keys callers expect,
      * forces ``mode: "paper"``,
      * points kill_switch at the literal ./KILL_SWITCH path,
    so the bot degrades to DRY_RUN rather than crashing. The runner is expected to emit a
    loud ``mandate_fallback`` audit event when this path is taken (ARCHITECTURE.md §6).
    The authoritative numbers always come from the JSON when present.
    """
    mandate_path = Path(path) if path is not None else MANDATE_FILE
    if mandate_path.exists():
        with open(mandate_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # SAFE fallback — no invented dollar limits; paper mode; literal kill switch.
    return {
        "account": "FALLBACK_NO_MANDATE_FILE",
        "rules": {},                       # empty => MandateView/risk_guard treat as unset
        "kill_switch": {"file": "./KILL_SWITCH", "auto_flatten_on_kill": True},
        "mode": "paper",
        "_fallback": True,                 # marker so runner can audit mandate_fallback
    }


def resolve_kill_switch_path(mandate: Optional[dict] = None) -> Path:
    """
    Resolve the kill-switch file path from the mandate, relative to vibe-trading/.

    Reads ``mandate["kill_switch"]["file"]`` (default "./KILL_SWITCH") and resolves it
    against VIBE_DIR. Only this literal path triggers a halt+flatten; KILL_SWITCH_DISABLED
    is NOT a trigger (ARCHITECTURE.md §0.3). If the mandate is None it is loaded here.
    """
    m = mandate if mandate is not None else load_mandate()
    rel = (m.get("kill_switch") or {}).get("file", "./KILL_SWITCH")
    candidate = Path(rel)
    if candidate.is_absolute():
        return candidate
    # Resolve relative entries (e.g. "./KILL_SWITCH") against vibe-trading/.
    return (VIBE_DIR / candidate).resolve()


def ensure_log_dirs(config: Optional[BotConfig] = None) -> Path:
    """Create the bot/logs/ and logs/sessions/ directories if absent; return log_dir."""
    log_dir = config.log_dir if config is not None else LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "sessions").mkdir(parents=True, exist_ok=True)
    return log_dir


def _parse_eod_flatten(raw_value: object) -> time:
    """
    Parse an ``eod_flatten_et`` override ("HH:MM") into a ``time``, FAIL-CLOSED.

    The EOD-flatten is a hard safety control backing the no-overnight mandate
    (overnight_holds=false / close_eod=true). On ANY parse error, or any value later than
    EOD_FLATTEN_MAX (15:55 ET) / at-or-past the RTH-close ceiling (16:00 ET), we log a
    LOUD warning and KEEP the safe default rather than raising or honouring an unsafe time.
    This module promises to degrade, not crash, and the safety control can never be
    disabled via config.
    """
    raw = str(raw_value).strip()
    try:
        parts = raw.split(":")
        if len(parts) < 2:
            raise ValueError(f"expected 'HH:MM', got {raw!r} (no ':' separator)")
        hh, mm = int(parts[0]), int(parts[1])
        candidate = time(hh, mm)  # raises ValueError for out-of-range like 99:99 / -1:00
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning(
            "config: UNPARSEABLE eod_flatten_et %r (%s) — KEEPING safe default %s ET",
            raw_value, exc, EOD_FLATTEN_DEFAULT,
        )
        return EOD_FLATTEN_DEFAULT

    if candidate >= EOD_FLATTEN_HARD_CEILING or candidate > EOD_FLATTEN_MAX:
        logger.warning(
            "config: eod_flatten_et %s is past the safety limit (max %s / close %s ET); "
            "this would allow a de-facto overnight hold (mandate overnight_holds=false). "
            "REJECTING and keeping safe default %s ET.",
            candidate, EOD_FLATTEN_MAX, EOD_FLATTEN_HARD_CEILING, EOD_FLATTEN_DEFAULT,
        )
        return EOD_FLATTEN_DEFAULT

    return candidate


def _parse_bounded_int(raw_value: object, default: int, *, name: str,
                       min_val: int, max_val: Optional[int] = None) -> int:
    """
    Coerce an override to ``int`` within [min_val, max_val], FAIL-CLOSED.

    On non-coercible input (e.g. '10.5', 'abc') or an out-of-range value we log a loud
    warning and return ``default`` rather than raising at startup. ``max_val`` (when given)
    guards safety ceilings (e.g. the consecutive-loss circuit-breaker) so a config file
    cannot silently disable a control by setting an absurd value.
    """
    try:
        value = int(raw_value)
    except (ValueError, TypeError):
        logger.warning(
            "config: %s=%r is not a valid integer — keeping safe default %s",
            name, raw_value, default,
        )
        return default
    if value < min_val:
        logger.warning(
            "config: %s=%s below minimum %s — keeping safe default %s",
            name, value, min_val, default,
        )
        return default
    if max_val is not None and value > max_val:
        logger.warning(
            "config: %s=%s exceeds safety ceiling %s — CLAMPING to %s so the control "
            "cannot be disabled via config", name, value, max_val, max_val,
        )
        return max_val
    return value


def _parse_bounded_float(raw_value: object, default: float, *, name: str,
                         low: float, high: float) -> float:
    """Coerce an override to ``float`` in the OPEN interval (low, high), FAIL-CLOSED."""
    try:
        value = float(raw_value)
    except (ValueError, TypeError):
        logger.warning(
            "config: %s=%r is not a valid number — keeping safe default %s",
            name, raw_value, default,
        )
        return default
    if not (low < value < high):
        logger.warning(
            "config: %s=%s outside allowed range (%s, %s) — keeping safe default %s",
            name, value, low, high, default,
        )
        return default
    return value


def _resolve_instrument(raw_value: object) -> str:
    """
    Enforce the Lucid instrument allowlist, FAIL-CLOSED.

    The mandate's ``instruments_allowed`` is authoritative (read from lucid_mandate.json).
    Any instrument not on the allowlist is REJECTED and coerced to the safe default 'ES'
    (which is itself verified to be on the allowlist) so config never hands a
    non-allowlisted instrument downstream. Defence is also present in risk_guard/
    traderpost, but config must fail-closed here too rather than fail-open.
    """
    instrument = str(raw_value).strip().upper()
    try:
        rules = load_mandate().get("rules", {}) or {}
        allowed = rules.get("instruments_allowed") or []
    except Exception as exc:  # never crash config on a mandate read hiccup
        logger.warning("config: could not load mandate for allowlist check (%s)", exc)
        allowed = []

    if not allowed:
        # No allowlist available (fallback mandate): only the hardcoded-safe 'ES' passes.
        if instrument != "ES":
            logger.warning(
                "config: instrument %r supplied but no mandate allowlist available — "
                "coercing to safe default 'ES'", raw_value,
            )
            return "ES"
        return "ES"

    if instrument not in allowed:
        logger.warning(
            "config: instrument %r is NOT on the mandate allowlist %s — coercing to safe "
            "default 'ES' (fail-closed)", raw_value, allowed,
        )
        return "ES" if "ES" in allowed else str(allowed[0])
    return instrument


def load_config(path: Optional[Path] = None) -> BotConfig:
    """
    Load operational config (NOT secrets, NOT mandate limits).

    ``go_live`` defaults to False. The live gate also requires HERMES_BOT_LIVE=1 (env), so
    the bot is paper/DRY_RUN unless BOTH are explicitly set. If a JSON config file is
    provided/exists it may override the operational tunables below, but it can NEVER carry
    secrets and is NOT a source of mandate limits. ``go_live: true`` in a config file is
    honoured only as the config half of the gate — the env half is still mandatory.

    Recognised JSON keys (all optional): go_live, instrument, poll_interval_sec,
    daily_gate_pct, consecutive_loss_limit, eod_flatten_et ("HH:MM"),
    strategy.{kill_zones,lookback,sweep_bars,msb_bars,ote_low,ote_high,tp1_rr,tp2_rr,
    default_contracts}. Any UNKNOWN top-level key is logged and ignored (see
    RECOGNISED_CONFIG_KEYS).

    FAIL-CLOSED CONTRACT: this function NEVER raises on a malformed/tampered config file.
    The file is untrusted external input; on a missing file, unreadable file, bad JSON, or
    any individual bad value, it emits an audit-able warning and falls back to the SAFE
    default for that field (and, in the worst case, to the all-defaults BotConfig — which
    is paper/DRY_RUN). This matches the docstring guarantee (§lines 36-37): degrade to
    DRY_RUN, never crash the runner at startup.
    """
    overrides: dict = {}
    if path is not None:
        cfg_path = Path(path)
        try:
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    overrides = loaded
                else:
                    logger.warning(
                        "config: %s did not contain a JSON object (got %s) — ignoring, "
                        "using safe defaults", cfg_path, type(loaded).__name__,
                    )
        except (OSError, ValueError) as exc:
            logger.warning(
                "config: could not read/parse config file %s (%s) — falling back to safe "
                "defaults (paper/DRY_RUN)", cfg_path, exc,
            )
            overrides = {}

    # Build everything defensively. If ANY unexpected error sneaks through, fail closed to
    # the all-defaults BotConfig rather than propagating an exception to the runner.
    try:
        # Whitelist enforcement: log and ignore unknown top-level keys.
        unknown = set(overrides) - RECOGNISED_CONFIG_KEYS
        if unknown:
            logger.warning(
                "config: ignoring unrecognised config keys %s (recognised: %s)",
                sorted(unknown), sorted(RECOGNISED_CONFIG_KEYS),
            )

        # Build StrategyConfig from any nested overrides, falling back to safe defaults.
        strat_over = overrides.get("strategy", {}) or {}
        base_strat = StrategyConfig()
        strategy = StrategyConfig(
            kill_zones=tuple(strat_over.get("kill_zones", base_strat.kill_zones)),
            lookback=_parse_bounded_int(
                strat_over.get("lookback", base_strat.lookback),
                base_strat.lookback, name="strategy.lookback", min_val=1),
            sweep_bars=_parse_bounded_int(
                strat_over.get("sweep_bars", base_strat.sweep_bars),
                base_strat.sweep_bars, name="strategy.sweep_bars", min_val=1),
            msb_bars=_parse_bounded_int(
                strat_over.get("msb_bars", base_strat.msb_bars),
                base_strat.msb_bars, name="strategy.msb_bars", min_val=1),
            ote_low=_parse_bounded_float(
                strat_over.get("ote_low", base_strat.ote_low),
                base_strat.ote_low, name="strategy.ote_low", low=0.0, high=1.0),
            ote_high=_parse_bounded_float(
                strat_over.get("ote_high", base_strat.ote_high),
                base_strat.ote_high, name="strategy.ote_high", low=0.0, high=1.0),
            tp1_rr=_parse_bounded_float(
                strat_over.get("tp1_rr", base_strat.tp1_rr),
                base_strat.tp1_rr, name="strategy.tp1_rr", low=0.0, high=1000.0),
            tp2_rr=_parse_bounded_float(
                strat_over.get("tp2_rr", base_strat.tp2_rr),
                base_strat.tp2_rr, name="strategy.tp2_rr", low=0.0, high=1000.0),
            default_contracts=_parse_bounded_int(
                strat_over.get("default_contracts", base_strat.default_contracts),
                base_strat.default_contracts, name="strategy.default_contracts", min_val=1),
        )

        # eod_flatten_et: parse + clamp to the no-overnight safety ceiling (fail-closed).
        eod = (_parse_eod_flatten(overrides["eod_flatten_et"])
               if "eod_flatten_et" in overrides else EOD_FLATTEN_DEFAULT)

        # go_live: STRICT identity check — only literal JSON boolean true arms the config
        # half. A stray truthy value (e.g. the string "false", or 1) must NOT arm it.
        go_live = overrides.get("go_live") is True
        if "go_live" in overrides and not isinstance(overrides.get("go_live"), bool):
            logger.warning(
                "config: go_live=%r is not a JSON boolean — treating as False (live gate "
                "config half stays OFF). Use literal true to arm.", overrides.get("go_live"),
            )

        config = BotConfig(
            go_live=go_live,                               # SAFE: never live from file alone
            instrument=_resolve_instrument(overrides.get("instrument", "ES")),
            poll_interval_sec=_parse_bounded_int(
                overrides.get("poll_interval_sec", 10), 10,
                name="poll_interval_sec", min_val=1),
            strategy=strategy,
            daily_gate_pct=_parse_bounded_float(
                overrides.get("daily_gate_pct", 0.80), 0.80,
                name="daily_gate_pct", low=0.0, high=1.0),
            consecutive_loss_limit=_parse_bounded_int(
                overrides.get("consecutive_loss_limit", 3), 3,
                name="consecutive_loss_limit", min_val=1,
                max_val=CONSECUTIVE_LOSS_CEILING),
            eod_flatten_et=eod,
        )
        return config
    except Exception as exc:  # absolute backstop — degrade to all-safe defaults
        logger.warning(
            "config: unexpected error building config from %s (%s) — falling back to "
            "all-default BotConfig (paper/DRY_RUN)", path, exc,
        )
        return BotConfig()


# ── __main__ smoke test (no I/O beyond reading the mandate; POSTs nothing) ──────────

if __name__ == "__main__":
    print("config.py smoke test")
    print("=" * 60)

    cfg = load_config()
    print(f"BotConfig.go_live            : {cfg.go_live}            (expect False)")
    print(f"BotConfig.instrument         : {cfg.instrument}")
    print(f"BotConfig.poll_interval_sec  : {cfg.poll_interval_sec}")
    print(f"BotConfig.daily_gate_pct     : {cfg.daily_gate_pct}")
    print(f"BotConfig.consecutive_loss   : {cfg.consecutive_loss_limit}")
    print(f"BotConfig.eod_flatten_et     : {cfg.eod_flatten_et}      (expect 15:55:00)")
    print(f"StrategyConfig.kill_zones    : {cfg.strategy.kill_zones}")
    print(f"StrategyConfig.tp1_rr/tp2_rr : {cfg.strategy.tp1_rr} / {cfg.strategy.tp2_rr} (expect 2.0 / 4.0)")

    print("-" * 60)
    print(f"ENV live flag name           : {ENV_LIVE_FLAG} (must == {ENV_LIVE_VALUE!r})")
    print(f"TraderPost env var NAMES     : {TRADERPOST_ENV_VARS}")
    print(f"env_live_armed()             : {env_live_armed()}")
    print(f"is_live_enabled(cfg)         : {is_live_enabled(cfg)}   (expect False -> DRY_RUN)")
    assert is_live_enabled(cfg) is False, "SAFETY FAIL: live must be off by default"

    # Prove the gate stays off with only one half armed.
    _saved = os.environ.get(ENV_LIVE_FLAG)
    os.environ[ENV_LIVE_FLAG] = ENV_LIVE_VALUE
    try:
        assert is_live_enabled(cfg) is False, "SAFETY FAIL: env half alone must NOT arm live"
        live_cfg = load_config()
        object.__setattr__(live_cfg, "go_live", True)  # simulate config half ON
        assert is_live_enabled(live_cfg) is True, "gate should arm when BOTH halves on"
        print("Gate check                   : env-only=OFF, env+go_live=ON  OK")
    finally:
        if _saved is None:
            os.environ.pop(ENV_LIVE_FLAG, None)
        else:
            os.environ[ENV_LIVE_FLAG] = _saved

    print("-" * 60)
    mandate = load_mandate()
    rules = mandate.get("rules", {})
    print(f"mandate file                 : {MANDATE_FILE}")
    print(f"mandate mode                 : {mandate.get('mode')}")
    print(f"mandate instruments_allowed  : {rules.get('instruments_allowed')}")
    print(f"mandate max_loss_limit       : {rules.get('max_loss_limit')} (read from JSON, not hardcoded)")
    print(f"mandate max_position_size    : {rules.get('max_position_size')}")
    print(f"mandate daily_trade_cap      : {rules.get('daily_trade_cap')}")
    ks = resolve_kill_switch_path(mandate)
    print(f"kill switch path             : {ks}")
    print(f"kill switch ENGAGED?         : {ks.exists()}  (KILL_SWITCH_DISABLED does NOT count)")

    log_dir = ensure_log_dirs(cfg)
    print(f"log dir                      : {log_dir}  (exists={log_dir.exists()})")

    print("=" * 60)
    print("OK — config.py loaded; DRY_RUN by default, no secrets stored, no orders sent.")
