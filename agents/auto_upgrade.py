"""
ClawBot — Auto Upgrade Agent
Reads the latest code review report, uses LLM to generate and apply safe fixes,
commits to git, then signals for a bot restart.

Safety rules:
  - Only touches files explicitly listed in the code review
  - Never deletes files, never touches .env or config/
  - Applies one fix at a time, validates syntax before writing
  - Dry-run mode by default; set APPLY=True to commit changes
  - Creates a git branch per upgrade run for easy rollback
"""
from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clawbot.auto_upgrade")

ROOT        = Path(__file__).parent.parent
REVIEWS_DIR = ROOT / "data" / "code_reviews"
UPGRADE_LOG = ROOT / "data" / "logs" / "upgrades.log"

# Files the auto-upgrader is allowed to modify
_ALLOWED_FILES = {
    "core/brain.py",
    "core/scheduler.py",
    "core/conversation.py",
    "trading/strategy.py",
    "trading/trading_strategy.py",
    "trading/exchange.py",
    "trading/executor.py",
    "trading/backtest.py",
    "agents/news_filter_agent.py",
    "agents/sheets_agent.py",
    "agents/code_review_agent.py",
    "content/receiver.py",
    "dashboard/app.py",
}

# Files absolutely never touched
_FORBIDDEN = {"config/", ".env", "security/", "requirements.txt", "data/"}


def _latest_review() -> Optional[Path]:
    """Return path to the most recent code review markdown file."""
    if not REVIEWS_DIR.exists():
        return None
    files = sorted(REVIEWS_DIR.glob("*.md"), reverse=True)
    return files[0] if files else None


def _log_upgrade(entry: dict) -> None:
    UPGRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(UPGRADE_LOG, "a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(entry) + "\n")


def _validate_python(code: str) -> tuple[bool, str]:
    """Check Python syntax. Returns (ok, error_msg)."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


def _git_create_branch(branch: str) -> bool:
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=ROOT, check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _git_commit(files: list[str], message: str) -> bool:
    try:
        subprocess.run(["git", "add"] + files, cwd=ROOT, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=ROOT, check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Git commit failed: {e}")
        return False


def _extract_issues_from_review(review_text: str) -> str:
    """Pull HIGH priority issues section from the review markdown."""
    lines = review_text.splitlines()
    in_section = False
    issues = []
    for line in lines:
        if "HIGH" in line.upper() or "priority" in line.lower():
            in_section = True
        if in_section:
            issues.append(line)
        if len(issues) > 80:
            break
    return "\n".join(issues) if issues else review_text[:3000]


def _ask_llm_for_fix(file_path: str, file_content: str, issue_description: str) -> Optional[str]:
    """
    Ask Ollama (or Haiku fallback) to generate a targeted fix for one issue.
    Returns the full corrected file content, or None if it can't generate one.
    """
    prompt = f"""You are a Python expert fixing bugs in a trading bot.

FILE: {file_path}

ISSUE TO FIX:
{issue_description}

CURRENT FILE CONTENT:
```python
{file_content[:6000]}
```

Instructions:
1. Fix ONLY the specific issue described above
2. Do not refactor, rename, or restructure anything else
3. Return ONLY the corrected complete Python file content — no explanations, no markdown fences
4. If you cannot safely fix this issue, respond with exactly: CANNOT_FIX"""

    # Try Ollama first
    try:
        import ollama
        resp = ollama.chat(
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
            messages=[{"role": "user", "content": prompt}],
        )
        result = resp["message"]["content"].strip()
        if result == "CANNOT_FIX":
            return None
        # Strip markdown fences if present
        if result.startswith("```python"):
            result = result[9:]
        if result.startswith("```"):
            result = result[3:]
        if result.endswith("```"):
            result = result[:-3]
        return result.strip()
    except Exception as e:
        logger.warning(f"Ollama failed: {e}, trying Claude Haiku")

    # Haiku fallback
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        result = msg.content[0].text.strip()
        if result == "CANNOT_FIX":
            return None
        if result.startswith("```python"):
            result = result[9:]
        if result.startswith("```"):
            result = result[3:]
        if result.endswith("```"):
            result = result[:-3]
        return result.strip()
    except Exception as e:
        logger.error(f"Both LLMs failed: {e}")
        return None


def run_auto_upgrade(dry_run: bool = True, max_fixes: int = 3) -> dict:
    """
    Main auto-upgrade pipeline:
    1. Read latest code review
    2. Extract HIGH priority issues per file
    3. Generate fix via LLM
    4. Validate Python syntax
    5. Write fix + git commit (unless dry_run)

    Returns summary dict.
    """
    review_path = _latest_review()
    if not review_path:
        return {"status": "no_review", "message": "No code review found. Run /codereview first."}

    review_text = review_path.read_text(encoding="utf-8")
    logger.info(f"Auto-upgrade reading review: {review_path.name}")

    # Parse issues per file from the review
    # Look for sections like "### trading/strategy.py" with HIGH priority bullets
    import re
    file_sections: dict[str, str] = {}
    current_file = None
    current_lines: list[str] = []

    for line in review_text.splitlines():
        # Match "### path/to/file.py" or "## path/to/file.py"
        match = re.match(r"#{2,3}\s+([\w/._-]+\.py)", line)
        if match:
            if current_file and current_lines:
                file_sections[current_file] = "\n".join(current_lines)
            current_file = match.group(1)
            current_lines = []
        elif current_file:
            current_lines.append(line)

    if current_file and current_lines:
        file_sections[current_file] = "\n".join(current_lines)

    # Filter to allowed files with HIGH issues
    candidates = []
    for fpath, section in file_sections.items():
        if fpath not in _ALLOWED_FILES:
            continue
        if any(kw in section.upper() for kw in ["HIGH", "BUG", "ERROR", "CRITICAL", "FIX"]):
            candidates.append((fpath, section))

    if not candidates:
        return {"status": "nothing_to_fix", "message": "No HIGH priority issues found in latest review."}

    summary = {
        "status": "ok",
        "review": review_path.name,
        "dry_run": dry_run,
        "fixes_attempted": 0,
        "fixes_applied": 0,
        "fixes_failed": 0,
        "details": [],
    }

    branch_created = False

    for fpath, issue_desc in candidates[:max_fixes]:
        full_path = ROOT / fpath
        if not full_path.exists():
            logger.warning(f"File not found: {fpath}")
            continue

        # Safety: never touch forbidden paths
        if any(fpath.startswith(f) for f in _FORBIDDEN):
            logger.warning(f"Skipping forbidden file: {fpath}")
            continue

        current_content = full_path.read_text(encoding="utf-8")
        logger.info(f"Generating fix for {fpath}...")
        summary["fixes_attempted"] += 1

        new_content = _ask_llm_for_fix(fpath, current_content, issue_desc)
        if not new_content:
            logger.warning(f"LLM could not generate fix for {fpath}")
            summary["fixes_failed"] += 1
            summary["details"].append({"file": fpath, "result": "llm_refused"})
            continue

        # Validate syntax before writing
        ok, err = _validate_python(new_content)
        if not ok:
            logger.error(f"Generated fix for {fpath} has syntax error: {err}")
            summary["fixes_failed"] += 1
            summary["details"].append({"file": fpath, "result": "syntax_error", "error": err})
            continue

        if dry_run:
            logger.info(f"[DRY RUN] Would apply fix to {fpath}")
            summary["details"].append({"file": fpath, "result": "dry_run_ok"})
            summary["fixes_applied"] += 1
            continue

        # Create git branch on first real fix
        if not branch_created:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
            branch = f"auto-upgrade/{ts}"
            _git_create_branch(branch)
            branch_created = True

        # Write the fix
        full_path.write_text(new_content, encoding="utf-8")
        committed = _git_commit([str(full_path)], f"auto-upgrade: fix {fpath}")

        result = "committed" if committed else "written_no_commit"
        summary["fixes_applied"] += 1
        summary["details"].append({"file": fpath, "result": result})
        _log_upgrade({"file": fpath, "result": result, "review": review_path.name})
        logger.info(f"✅ Applied fix to {fpath} ({result})")

    return summary


def format_upgrade_message(summary: dict) -> str:
    """Format upgrade summary for Telegram."""
    if summary["status"] == "no_review":
        return f"🤖 <b>Auto-Upgrade</b>\n\n{summary['message']}"
    if summary["status"] == "nothing_to_fix":
        return f"✅ <b>Auto-Upgrade</b>\n\nNo HIGH priority issues found — bot is clean!"

    mode = "DRY RUN" if summary.get("dry_run") else "LIVE"
    lines = [
        f"🤖 <b>Auto-Upgrade [{mode}]</b>",
        f"📋 Review: <code>{summary['review']}</code>",
        f"🔧 Attempted: {summary['fixes_attempted']}",
        f"✅ Applied: {summary['fixes_applied']}",
        f"❌ Failed: {summary['fixes_failed']}",
        "",
    ]
    for d in summary.get("details", []):
        icon = "✅" if d["result"] in ("committed", "written_no_commit", "dry_run_ok") else "❌"
        lines.append(f"{icon} <code>{d['file']}</code> — {d['result']}")

    if not summary.get("dry_run") and summary["fixes_applied"] > 0:
        lines.append("\n🔄 <i>Send /restart to apply changes.</i>")
    elif summary.get("dry_run"):
        lines.append("\n<i>Dry run — no files changed. Send /upgrade apply to execute.</i>")

    return "\n".join(lines)
