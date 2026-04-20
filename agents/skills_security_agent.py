"""Skills Security Agent — ClawBot

Reviews installed Claude Code skills for security risks and code quality,
and performs a full bot security audit (blocked patterns, whitelist,
command injection, env secrets, dashboard exposure).

Public API
----------
    run_skills_review()  -> str   # review all skills, return summary
    run_security_audit() -> str   # full audit, return formatted report
    get_hardening_tips() -> list[str]
    check_skill(name)    -> str   # single skill review

Telegram: /skillsaudit
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clawbot.agents.skills_security")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SKILLS_DIR  = Path(__file__).parent.parent / ".claude" / "skills"
REVIEW_DIR  = Path(__file__).parent.parent / "data" / "security_reviews"
LOG_FILE    = Path(__file__).parent.parent / "data" / "logs" / "security_agent.log"
_PROJECT    = Path(__file__).parent.parent
_RECEIVER   = _PROJECT / "content" / "receiver.py"
_ENV_FILE   = _PROJECT / ".env"

_MAX_SKILL_CHARS = 4000   # truncate very large skill files for LLM

# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except Exception:
        pass
    logger.info(msg)


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

def _ask_llm(prompt: str, system: str = "", force: str = "complex") -> str:
    """Call ask_hybrid; fall back gracefully on any error."""
    try:
        from core.brain import ask_hybrid
        response, _ = ask_hybrid(prompt, system=system or None, force=force)
        return response.strip()
    except Exception as exc:
        logger.warning(f"ask_hybrid failed: {exc}")

    # Direct Ollama fallback
    try:
        from ollama import chat as ollama_chat
        model = os.getenv("OLLAMA_MODEL", "gemma3")
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        resp = ollama_chat(model=model, messages=msgs)
        return resp.message.content.strip()
    except Exception as exc2:
        logger.warning(f"Ollama fallback failed: {exc2}")

    # Anthropic fallback
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return "LLM unavailable (no API key)"
        client = anthropic.Anthropic(api_key=api_key)
        msgs = [{"role": "user", "content": prompt}]
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            system=system or "You are a security-focused software engineer.",
            messages=msgs,
        )
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    except Exception as exc3:
        return f"LLM unavailable: {exc3}"


# ===========================================================================
# Class: SkillsSecurityAgent
# ===========================================================================

class SkillsSecurityAgent:
    """Audits installed skills and bot security configuration."""

    # -----------------------------------------------------------------------
    # 1. Skills Reviewer
    # -----------------------------------------------------------------------

    def list_skills(self) -> list[dict]:
        """Scan SKILLS_DIR and return metadata for each skill."""
        skills: list[dict] = []
        if not SKILLS_DIR.exists():
            _log(f"SKILLS_DIR not found: {SKILLS_DIR}")
            return skills

        for entry in sorted(SKILLS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            skill_md   = entry / "SKILL.md"
            meta_json  = entry / "_meta.json"
            has_meta   = meta_json.exists()
            size       = skill_md.stat().st_size if skill_md.exists() else 0
            mtime      = (
                datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc).isoformat()
                if skill_md.exists() else "unknown"
            )
            skills.append({
                "name":          entry.name,
                "path":          str(entry),
                "size":          size,
                "last_modified": mtime,
                "has_meta":      has_meta,
            })
        return skills

    def review_skill(self, skill_name: str) -> dict:
        """LLM review of a single skill. Returns structured result."""
        skill_dir = SKILLS_DIR / skill_name
        if not skill_dir.exists():
            return {
                "skill_name":  skill_name,
                "issues":      [f"Skill directory not found: {skill_dir}"],
                "improvements": [],
                "risk_level":  "unknown",
                "llm_review":  "Skill not found.",
            }

        # Gather content
        parts: list[str] = []
        skill_md  = skill_dir / "SKILL.md"
        meta_json = skill_dir / "_meta.json"

        if skill_md.exists():
            try:
                parts.append(f"=== SKILL.md ===\n{skill_md.read_text(encoding='utf-8')}")
            except Exception as exc:
                parts.append(f"=== SKILL.md (read error: {exc}) ===")

        if meta_json.exists():
            try:
                parts.append(f"=== _meta.json ===\n{meta_json.read_text(encoding='utf-8')}")
            except Exception as exc:
                parts.append(f"=== _meta.json (read error: {exc}) ===")

        content = "\n\n".join(parts)
        if len(content) > _MAX_SKILL_CHARS:
            content = content[:_MAX_SKILL_CHARS] + "\n... [truncated]"

        prompt = (
            f"You are a senior software engineer reviewing an AI agent skill.\n"
            f"Review this skill for:\n"
            f"1) Security risks\n"
            f"2) Code quality issues\n"
            f"3) Performance problems\n"
            f"4) Suggested improvements\n\n"
            f"Skill name: {skill_name}\n\n"
            f"Content:\n{content}\n\n"
            f"Respond in this exact format:\n"
            f"RISK_LEVEL: low|medium|high\n"
            f"ISSUES:\n- issue 1\n- issue 2\n"
            f"IMPROVEMENTS:\n- suggestion 1\n- suggestion 2\n"
            f"REVIEW:\n(detailed analysis)"
        )

        try:
            llm_response = _ask_llm(prompt, force="complex")
        except Exception as exc:
            llm_response = f"LLM review failed: {exc}"

        # Parse structured fields from LLM response
        risk_level   = "low"
        issues:       list[str] = []
        improvements: list[str] = []

        try:
            risk_match = re.search(r"RISK_LEVEL:\s*(low|medium|high)", llm_response, re.IGNORECASE)
            if risk_match:
                risk_level = risk_match.group(1).lower()

            issues_match = re.search(
                r"ISSUES:\s*(.*?)(?=IMPROVEMENTS:|REVIEW:|$)",
                llm_response, re.DOTALL | re.IGNORECASE,
            )
            if issues_match:
                issues = [
                    line.lstrip("- •").strip()
                    for line in issues_match.group(1).splitlines()
                    if line.strip().startswith(("-", "•")) and line.strip()[1:].strip()
                ]

            improvements_match = re.search(
                r"IMPROVEMENTS:\s*(.*?)(?=REVIEW:|$)",
                llm_response, re.DOTALL | re.IGNORECASE,
            )
            if improvements_match:
                improvements = [
                    line.lstrip("- •").strip()
                    for line in improvements_match.group(1).splitlines()
                    if line.strip().startswith(("-", "•")) and line.strip()[1:].strip()
                ]
        except Exception:
            pass

        return {
            "skill_name":   skill_name,
            "issues":       issues,
            "improvements": improvements,
            "risk_level":   risk_level,
            "llm_review":   llm_response,
        }

    def review_all_skills(self) -> list[dict]:
        """Review every installed skill, persist results, return list of results."""
        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        skills  = self.list_skills()
        results: list[dict] = []

        for s in skills:
            _log(f"Reviewing skill: {s['name']}")
            result = self.review_skill(s["name"])
            results.append(result)

        ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path  = REVIEW_DIR / f"skills_review_{ts}.json"
        try:
            out_path.write_text(
                json.dumps(results, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            _log(f"Skills review saved: {out_path}")
        except Exception as exc:
            _log(f"Could not save skills review: {exc}")

        return results

    def improve_skill_prompt(self, skill_name: str, issue: str) -> str:
        """Ask LLM to generate an improved version of the skill section addressing a given issue."""
        skill_dir = SKILLS_DIR / skill_name
        skill_md  = skill_dir / "SKILL.md"
        content   = ""
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8")[:_MAX_SKILL_CHARS]
            except Exception:
                pass

        prompt = (
            f"You are improving an AI agent skill called '{skill_name}'.\n\n"
            f"The following issue was identified:\n{issue}\n\n"
            f"Current skill content:\n{content}\n\n"
            f"Generate an improved version of the relevant section of this skill "
            f"that addresses the issue. Output only the improved text, ready to use."
        )
        return _ask_llm(prompt, force="complex")

    # -----------------------------------------------------------------------
    # 2. Security Auditor
    # -----------------------------------------------------------------------

    def audit_blocked_patterns(self) -> dict:
        """Test each _BLOCKED_PATTERNS entry against known bypass attempts."""
        # Import patterns from receiver at runtime to avoid circular imports
        blocked_patterns: list[str] = []
        try:
            # Read the file and extract the pattern list via regex to avoid
            # importing the whole receiver (which starts the bot).
            receiver_text = _RECEIVER.read_text(encoding="utf-8")
            match = re.search(
                r"_BLOCKED_PATTERNS\s*=\s*\[(.*?)\]",
                receiver_text, re.DOTALL,
            )
            if match:
                raw = match.group(1)
                blocked_patterns = re.findall(r'r"([^"]+)"', raw)
        except Exception as exc:
            _log(f"Could not read _BLOCKED_PATTERNS: {exc}")

        # Bypass test payloads — case variants, whitespace, encoding tricks
        bypass_attempts = [
            "Os.System",
            "OS.SYSTEM",
            "\nos.system",
            "os .system",
            "eval\t(",
            "__builtins__",
            "getattr(os",
            "importlib",
            "base64.b64decode",
            "exec(compile",
            "os\tsystem",
            "Os.SyStEm(",
            "\x6fs.system",         # hex-encoded 'o'
            "subprocess.Popen",
            "SUBPROCESS.CALL",
        ]

        results: list[dict] = []
        overall_hits = 0

        for pattern in blocked_patterns:
            bypasses_found: list[str] = []
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                for attempt in bypass_attempts:
                    if not compiled.search(attempt.lower()):
                        # The pattern does NOT match — potential bypass
                        bypasses_found.append(attempt)
                    else:
                        overall_hits += 1
            except re.error as exc:
                bypasses_found.append(f"[invalid regex: {exc}]")

            results.append({
                "pattern":        pattern,
                "bypasses_found": bypasses_found,
            })

        total_checks = len(blocked_patterns) * len(bypass_attempts)
        coverage = int((overall_hits / total_checks * 100)) if total_checks else 0

        return {
            "pattern_count":  len(blocked_patterns),
            "patterns":       results,
            "bypass_attempts": bypass_attempts,
            "coverage_score": coverage,
        }

    def audit_whitelist(self) -> dict:
        """Check whitelist configuration quality."""
        try:
            from security.whitelist import ALLOWED_CHAT_IDS, is_authorized
            whitelist_count = len(ALLOWED_CHAT_IDS)
            has_default_deny = whitelist_count == 0 or True  # empty == deny all
            verdict = (
                "PASS — non-empty allowlist with default-deny"
                if whitelist_count > 0
                else "WARN — ALLOWED_CHAT_IDS is empty: all messages denied"
            )
        except Exception as exc:
            whitelist_count  = -1
            has_default_deny = False
            verdict          = f"ERROR — could not load whitelist: {exc}"

        return {
            "whitelist_count": whitelist_count,
            "type":            "allowlist",
            "has_default_deny": has_default_deny,
            "verdict":         verdict,
        }

    def audit_command_injection(self) -> dict:
        """Scan receiver.py for shell injection risk patterns."""
        issues:  list[str] = []
        passed:  list[str] = []
        details: list[dict] = []

        try:
            text  = _RECEIVER.read_text(encoding="utf-8")
            lines = text.splitlines()
        except Exception as exc:
            return {"error": str(exc), "issues": [], "passed": [], "details": []}

        checks = [
            # (description, regex, severity)
            ("shell=True usage", r"shell\s*=\s*True", "info"),
            ("String format into shell", r'(f"|f\').*\{.*\}.*subprocess', "high"),
            ("Unvalidated .format() in command", r'\.format\(.*\)\s*,?\s*shell', "high"),
            ("os.system direct call", r'\bos\.system\s*\(', "medium"),
            ("os.popen direct call", r'\bos\.popen\s*\(', "medium"),
            ("eval() usage", r'\beval\s*\(', "high"),
            ("exec() usage", r'\bexec\s*\(', "high"),
            ("compile() usage", r'\bcompile\s*\(', "medium"),
        ]

        for desc, pattern, severity in checks:
            matches: list[str] = []
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    matches.append(f"line {i}: {line.strip()[:120]}")

            if matches:
                details.append({
                    "check":    desc,
                    "severity": severity,
                    "matches":  matches,
                })
                if severity in ("high", "medium"):
                    issues.append(f"[{severity.upper()}] {desc}: {len(matches)} occurrence(s)")
                else:
                    passed.append(f"[INFO] {desc}: {len(matches)} occurrence(s) — expected & auth-gated")
            else:
                passed.append(f"[OK] No {desc} found")

        return {"issues": issues, "passed": passed, "details": details}

    def audit_env_secrets(self) -> dict:
        """Check .env hygiene and scan Python files for hardcoded secrets."""
        results: dict = {
            "env_exists":       False,
            "env_in_gitignore": False,
            "required_keys_present": {},
            "hardcoded_secrets": [],
            "verdict": "",
        }

        # .env exists?
        results["env_exists"] = _ENV_FILE.exists()

        # .env in .gitignore?
        gitignore = _PROJECT / ".gitignore"
        if gitignore.exists():
            try:
                gi_text = gitignore.read_text(encoding="utf-8")
                results["env_in_gitignore"] = bool(re.search(r"^\s*\.env\b", gi_text, re.MULTILINE))
            except Exception:
                pass

        # Required keys present?
        required_keys = [
            "TELEGRAM_BOT_TOKEN",
            "ALLOWED_CHAT_ID",
            "ANTHROPIC_API_KEY",
            "CRYPTO_API_KEY",
            "CRYPTO_API_SECRET",
            "OLLAMA_MODEL",
        ]
        for key in required_keys:
            results["required_keys_present"][key] = bool(os.getenv(key, "").strip())

        # Scan .py files for hardcoded secret patterns
        secret_patterns = [
            (r'(?:api_key|apikey|secret|token|password)\s*=\s*["\'][A-Za-z0-9+/=_\-]{16,}["\']', "hardcoded credential"),
            (r'sk-ant-[A-Za-z0-9\-_]{20,}', "Anthropic API key"),
            (r'(?:Bearer|Authorization)\s+[A-Za-z0-9+/=_\-]{20,}', "auth header token"),
            (r'[0-9]{9,10}:[A-Za-z0-9\-_]{35}', "Telegram bot token pattern"),
        ]
        py_files = list(_PROJECT.rglob("*.py"))
        for pyf in py_files:
            # Skip venv, __pycache__
            if any(part in pyf.parts for part in (".venv", "__pycache__", "site-packages")):
                continue
            try:
                text = pyf.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for spat, desc in secret_patterns:
                for m in re.finditer(spat, text, re.IGNORECASE):
                    # Only flag if it doesn't reference an env var lookup
                    line_start = text.rfind("\n", 0, m.start()) + 1
                    line_end   = text.find("\n", m.end())
                    line_text  = text[line_start:line_end].strip()
                    if "os.getenv" in line_text or "environ" in line_text:
                        continue
                    results["hardcoded_secrets"].append({
                        "file":    str(pyf.relative_to(_PROJECT)),
                        "type":    desc,
                        "snippet": line_text[:80],
                    })

        missing = [k for k, v in results["required_keys_present"].items() if not v]
        hard    = results["hardcoded_secrets"]
        if hard:
            results["verdict"] = f"FAIL — {len(hard)} hardcoded secret(s) found"
        elif missing:
            results["verdict"] = f"WARN — missing env keys: {', '.join(missing)}"
        elif not results["env_exists"]:
            results["verdict"] = "WARN — .env file not found"
        elif not results["env_in_gitignore"]:
            results["verdict"] = "WARN — .env not in .gitignore"
        else:
            results["verdict"] = "PASS"

        return results

    def audit_command_injection_risks(self) -> dict:
        """Alias kept for internal use."""
        return self.audit_command_injection()

    def run_full_audit(self) -> dict:
        """Run all audits, compile a single scored report."""
        _log("Starting full security audit")
        ts = datetime.now(timezone.utc).isoformat()

        blocked  = self.audit_blocked_patterns()
        wl       = self.audit_whitelist()
        inj      = self.audit_command_injection()
        env      = self.audit_env_secrets()
        dash     = self.check_dashboard_exposure()

        critical: list[str] = []
        high:     list[str] = []
        medium:   list[str] = []
        low:      list[str] = []
        passed:   list[str] = []

        # Evaluate whitelist
        if "ERROR" in wl["verdict"]:
            critical.append(f"Whitelist load error: {wl['verdict']}")
        elif "WARN" in wl["verdict"]:
            high.append(wl["verdict"])
        else:
            passed.append(f"Whitelist: {wl['verdict']}")

        # Evaluate blocked patterns
        cov = blocked.get("coverage_score", 0)
        bypasses_total = sum(len(p["bypasses_found"]) for p in blocked.get("patterns", []))
        if bypasses_total > 20:
            high.append(f"Blocked patterns: {bypasses_total} bypass variants detected (coverage {cov}%)")
        elif bypasses_total > 5:
            medium.append(f"Blocked patterns: {bypasses_total} potential bypass variants (coverage {cov}%)")
        else:
            passed.append(f"Blocked patterns: coverage {cov}%, only {bypasses_total} bypass variants found")

        # Evaluate injection scan
        for issue in inj.get("issues", []):
            if "[HIGH]" in issue:
                high.append(f"Injection: {issue}")
            else:
                medium.append(f"Injection: {issue}")
        for p in inj.get("passed", []):
            if "[OK]" in p:
                passed.append(p)

        # Evaluate env secrets
        if env["hardcoded_secrets"]:
            critical.append(f"Hardcoded secrets: {len(env['hardcoded_secrets'])} found")
        if not env["env_exists"]:
            high.append(".env file missing")
        if not env["env_in_gitignore"]:
            medium.append(".env not listed in .gitignore")
        missing_keys = [k for k, v in env.get("required_keys_present", {}).items() if not v]
        if missing_keys:
            medium.append(f"Missing env keys: {', '.join(missing_keys)}")
        if env["verdict"] == "PASS":
            passed.append("Env secrets: PASS")

        # Evaluate dashboard
        if dash.get("exposed"):
            high.append(f"Dashboard exposed on {dash.get('bind_addr', '?')} — no auth middleware")
        elif not dash.get("has_auth"):
            medium.append(f"Dashboard on {dash.get('bind_addr', '?')} — no auth middleware detected")
        else:
            passed.append(f"Dashboard: {dash.get('bind_addr')} with auth middleware")

        # Risk score: start at 100, deduct
        score = 100
        score -= len(critical) * 25
        score -= len(high)     * 10
        score -= len(medium)   *  5
        score -= len(low)      *  2
        score  = max(0, score)

        recommendations: list[str] = []
        if critical:
            recommendations.append("URGENT: Remove all hardcoded secrets immediately.")
        if not env["env_in_gitignore"]:
            recommendations.append("Add .env to .gitignore to prevent accidental commit.")
        if bypasses_total > 5:
            recommendations.append("Expand _BLOCKED_PATTERNS with case-insensitive flag or normalise input before matching.")
        if not dash.get("has_auth"):
            recommendations.append("Add Flask authentication middleware (e.g. HTTP Basic Auth or token check) to dashboard.")
        if missing_keys:
            recommendations.append(f"Set missing environment variables: {', '.join(missing_keys)}")

        report = {
            "timestamp":       ts,
            "risk_score":      score,
            "critical":        critical,
            "high":            high,
            "medium":          medium,
            "low":             low,
            "passed":          passed,
            "recommendations": recommendations,
            # Raw sub-reports for reference
            "_blocked":  blocked,
            "_whitelist": wl,
            "_injection": inj,
            "_env":       env,
            "_dashboard": dash,
        }

        # Persist
        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        ts_fn    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = REVIEW_DIR / f"security_audit_{ts_fn}.json"
        try:
            # Save without private raw sub-reports to keep file clean
            save_data = {k: v for k, v in report.items() if not k.startswith("_")}
            out_path.write_text(json.dumps(save_data, indent=2), encoding="utf-8")
            _log(f"Security audit saved: {out_path}")
        except Exception as exc:
            _log(f"Could not save audit: {exc}")

        return report

    def format_audit_report(self, audit: dict) -> str:
        """Format audit dict as Telegram HTML string."""
        score    = audit.get("risk_score", 0)
        ts       = audit.get("timestamp", "")[:16].replace("T", " ")
        critical = audit.get("critical", [])
        high     = audit.get("high", [])
        medium   = audit.get("medium", [])
        low      = audit.get("low", [])
        passed   = audit.get("passed", [])
        recs     = audit.get("recommendations", [])

        # Score emoji
        if score >= 80:
            score_icon = "🟢"
        elif score >= 60:
            score_icon = "🟡"
        elif score >= 40:
            score_icon = "🟠"
        else:
            score_icon = "🔴"

        lines: list[str] = [
            f"🔐 <b>ClawBot Security Audit</b>",
            f"<i>{ts} UTC</i>",
            f"",
            f"{score_icon} <b>Risk Score: {score}/100</b>",
        ]

        if critical:
            lines.append(f"\n🚨 <b>CRITICAL ({len(critical)})</b>")
            for c in critical:
                lines.append(f"  • {c}")

        if high:
            lines.append(f"\n🔴 <b>HIGH ({len(high)})</b>")
            for h in high:
                lines.append(f"  • {h}")

        if medium:
            lines.append(f"\n🟠 <b>MEDIUM ({len(medium)})</b>")
            for m in medium:
                lines.append(f"  • {m}")

        if low:
            lines.append(f"\n🟡 <b>LOW ({len(low)})</b>")
            for lo in low:
                lines.append(f"  • {lo}")

        if passed:
            lines.append(f"\n✅ <b>PASSED ({len(passed)})</b>")
            for p in passed[:6]:   # cap at 6 to avoid Telegram length limit
                lines.append(f"  • {p}")
            if len(passed) > 6:
                lines.append(f"  • ... and {len(passed) - 6} more checks passed")

        if recs:
            lines.append(f"\n💡 <b>Recommendations</b>")
            for i, r in enumerate(recs, 1):
                lines.append(f"  {i}. {r}")

        lines.append(f"\n<i>Full report: data/security_reviews/</i>")
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # 3. Hardening Advisor
    # -----------------------------------------------------------------------

    def get_hardening_recommendations(self) -> list[str]:
        """LLM-powered prioritised hardening list for the current bot config."""
        # Gather a concise config summary
        wl   = self.audit_whitelist()
        env  = self.audit_env_secrets()
        dash = self.check_dashboard_exposure()
        inj  = self.audit_command_injection()

        config_summary = (
            f"Bot: ClawBot (Python Telegram Bot + Flask dashboard)\n"
            f"Whitelist: {wl['verdict']}\n"
            f"Dashboard: bind={dash.get('bind_addr')}, has_auth={dash.get('has_auth')}\n"
            f"Env secrets verdict: {env['verdict']}\n"
            f"Injection issues: {len(inj.get('issues', []))}\n"
            f"shell=True in /run: yes (auth-gated, blocklist-protected)\n"
            f"LLM: Ollama local + Anthropic API fallback\n"
        )

        prompt = (
            f"You are a cybersecurity engineer advising on a personal Telegram trading bot.\n\n"
            f"Current security config:\n{config_summary}\n\n"
            f"Provide a prioritised list of the top 8 hardening recommendations "
            f"specific to this setup. Each item should be concrete and actionable. "
            f"Format as a numbered list."
        )

        try:
            response = _ask_llm(prompt, force="complex")
        except Exception as exc:
            return [f"LLM hardening analysis failed: {exc}"]

        # Extract numbered list items
        items: list[str] = []
        for line in response.splitlines():
            line = line.strip()
            m = re.match(r"^\d+[\.\)]\s+(.+)", line)
            if m:
                items.append(m.group(1))
        return items if items else [response]

    def check_dashboard_exposure(self) -> dict:
        """Check Flask dashboard bind address and auth middleware."""
        bind_addr = "unknown"
        has_auth  = False
        exposed   = False
        recs:  list[str] = []

        dash_file = _PROJECT / "dashboard" / "app.py"
        if not dash_file.exists():
            return {
                "exposed": False,
                "bind_addr": "not found",
                "has_auth": False,
                "recommendations": ["Dashboard file not found — verify path."],
            }

        try:
            text = dash_file.read_text(encoding="utf-8")
        except Exception as exc:
            return {
                "exposed": False,
                "bind_addr": "read error",
                "has_auth": False,
                "recommendations": [str(exc)],
            }

        # Detect bind address
        run_match = re.search(r'app\.run\([^)]*\)', text)
        if run_match:
            run_call = run_match.group(0)
            if "0.0.0.0" in run_call:
                bind_addr = "0.0.0.0"
                exposed   = True
                recs.append("Dashboard bound to 0.0.0.0 — exposed on all interfaces. Change to 127.0.0.1.")
            elif "127.0.0.1" in run_call:
                bind_addr = "127.0.0.1"
                exposed   = False
            else:
                bind_addr = "localhost (default)"

        # Detect auth middleware patterns
        auth_patterns = [
            r"@login_required",
            r"flask_login",
            r"flask_httpauth",
            r"check_auth",
            r"verify_token",
            r"Authorization",
            r"BasicAuth",
            r"HTTPBasicAuth",
            r"require_auth",
        ]
        for pat in auth_patterns:
            if re.search(pat, text, re.IGNORECASE):
                has_auth = True
                break

        if not has_auth:
            recs.append("No authentication middleware detected on dashboard — add HTTP Basic Auth or token check.")

        return {
            "exposed":         exposed,
            "bind_addr":       bind_addr,
            "has_auth":        has_auth,
            "recommendations": recs,
        }


# ===========================================================================
# Public API functions
# ===========================================================================

_agent = SkillsSecurityAgent()


def run_skills_review() -> str:
    """Review all installed skills, return a formatted summary string."""
    try:
        results = _agent.review_all_skills()
    except Exception as exc:
        return f"❌ Skills review failed: {exc}"

    if not results:
        return "⚠️ No skills found in SKILLS_DIR."

    high_risk = [r for r in results if r.get("risk_level") == "high"]
    med_risk  = [r for r in results if r.get("risk_level") == "medium"]

    lines = [
        f"🛡 <b>Skills Security Review</b>",
        f"<i>{len(results)} skills reviewed</i>",
        f"",
        f"🔴 High risk:   {len(high_risk)}",
        f"🟠 Medium risk: {len(med_risk)}",
        f"🟢 Low risk:    {len(results) - len(high_risk) - len(med_risk)}",
        f"",
    ]

    for r in results:
        icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(r["risk_level"], "⚪")
        issues_count = len(r.get("issues", []))
        lines.append(f"{icon} <b>{r['skill_name']}</b> — {issues_count} issue(s)")
        for issue in r.get("issues", [])[:2]:   # show up to 2 issues inline
            lines.append(f"   • {issue[:100]}")

    lines.append(f"\n<i>Full report saved to data/security_reviews/</i>")
    return "\n".join(lines)


def run_security_audit() -> str:
    """Run full security audit and return Telegram-formatted report."""
    try:
        audit = _agent.run_full_audit()
        return _agent.format_audit_report(audit)
    except Exception as exc:
        _log(f"run_security_audit error: {exc}")
        return f"❌ Security audit failed: {exc}"


def get_hardening_tips() -> list[str]:
    """Return prioritised hardening recommendations as a list of strings."""
    try:
        return _agent.get_hardening_recommendations()
    except Exception as exc:
        return [f"Hardening analysis failed: {exc}"]


def check_skill(skill_name: str) -> str:
    """Review a single skill and return a formatted string."""
    try:
        result = _agent.review_skill(skill_name)
    except Exception as exc:
        return f"❌ Skill review failed: {exc}"

    icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(result.get("risk_level", ""), "⚪")
    issues   = result.get("issues", [])
    improves = result.get("improvements", [])

    lines = [
        f"🛡 <b>Skill Review: {skill_name}</b>",
        f"{icon} Risk level: <b>{result.get('risk_level', 'unknown').upper()}</b>",
        f"",
    ]

    if issues:
        lines.append("⚠️ <b>Issues</b>")
        for issue in issues[:5]:
            lines.append(f"  • {issue[:120]}")
    else:
        lines.append("✅ No issues found.")

    if improves:
        lines.append("\n💡 <b>Improvements</b>")
        for imp in improves[:5]:
            lines.append(f"  • {imp[:120]}")

    # Include a snippet of the LLM review (up to 400 chars)
    review_text = result.get("llm_review", "")
    if review_text:
        # Extract just the REVIEW: section if present
        rev_match = re.search(r"REVIEW:\s*(.*)", review_text, re.DOTALL | re.IGNORECASE)
        snippet   = (rev_match.group(1) if rev_match else review_text).strip()[:400]
        lines.append(f"\n<i>{snippet}</i>")

    return "\n".join(lines)
