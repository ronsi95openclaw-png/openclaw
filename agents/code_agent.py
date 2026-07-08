"""
Code Agent — ClawBot
=====================
Secure file read/write/run agent with:
- Path traversal protection (all paths resolved against WORKSPACE_ROOT)
- Prompt injection guard (file contents wrapped in delimiters)
- Confirmation token system (5-min expiry for write/delete ops)
- Syntax check before write (compile() in temp file)
- Rotating audit log (timestamp, user_id, action, file, outcome)
- Subprocess hardening (shell=False, 30s timeout, output truncation)
- Whitelist-first (is_authorized checked before all operations)

Telegram commands:
  /code read <file>         — show file contents with syntax highlighting
  /code write <file>        — AI writes/edits file (with confirmation)
  /code run <file>          — execute file (with confirmation)
  /code ls [dir]            — list files in directory
  /code diff <file>         — show recent git diff for file
  /code yes <token>         — confirm pending operation
  /code no                  — cancel pending operation
"""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from security.whitelist import is_authorized

# ── Constants ─────────────────────────────────────────────────────────────────

WORKSPACE_ROOT = Path(__file__).parent.parent.resolve()
_AUDIT_LOG     = WORKSPACE_ROOT / "data" / "logs" / "command_audit.log"
_TOKEN_TTL     = 300       # 5 minutes
_MAX_FILE_SIZE = 50_000    # 50 KB max read
_MAX_OUTPUT    = 3_000     # truncate subprocess output
_ALLOWED_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml",
    ".toml", ".env.example", ".sh", ".bat",
}

# chat_id → {token, action, file_path, new_content, expires_at}
_pending: dict[int, dict] = {}


# ── Path safety ───────────────────────────────────────────────────────────────

def _safe_path(raw: str) -> Path:
    """Resolve path and ensure it's inside WORKSPACE_ROOT.

    Hardening layers (applied in order):
    1. URL-decode to catch %2e%2e%2f-style encoded traversal.
    2. Reject any input that is an absolute path (starts with / \\ or drive
       letter like C:) — callers always supply workspace-relative paths.
    3. Resolve against WORKSPACE_ROOT and confirm the result is still
       inside it (catches .. traversal, symlink escapes, etc.).
    4. Confirm the resolved path string starts with WORKSPACE_ROOT using
       os.path.commonpath to avoid prefix-collision edge cases.
    """
    import os
    from urllib.parse import unquote

    # Layer 1: decode URL-encoded characters (%2e%2e%2f → ../)
    decoded = unquote(raw)

    # Layer 2a: reject inputs containing .. sequences (catches ....// and similar
    # obfuscated traversal patterns before they reach the filesystem resolver).
    # Normalise slashes first so both / and \ variants are caught.
    normalised = decoded.replace("\\", "/")
    for part in normalised.replace("//", "/").split("/"):
        if part.startswith(".."):
            raise ValueError(f"Path traversal blocked: {raw!r}")

    # Layer 2b: reject absolute paths (Unix-style /... or Windows C:\... or \\UNC)
    decoded_stripped = decoded.lstrip()
    if (
        decoded_stripped.startswith("/")
        or decoded_stripped.startswith("\\")
        or (len(decoded_stripped) >= 2 and decoded_stripped[1] == ":")
    ):
        raise ValueError(f"Path traversal blocked: {raw!r}")

    # Layer 3 & 4: resolve and confirm containment
    p = (WORKSPACE_ROOT / decoded_stripped).resolve()
    try:
        common = os.path.commonpath([str(p), str(WORKSPACE_ROOT)])
    except ValueError:
        # commonpath raises ValueError when paths are on different drives
        raise ValueError(f"Path traversal blocked: {raw!r}")
    if common != str(WORKSPACE_ROOT):
        raise ValueError(f"Path traversal blocked: {raw!r}")
    return p


# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit(
    user_id: int,
    action: str,
    file_path,
    outcome: str,
    detail: str = "",
) -> None:
    """Write a JSONL audit entry. Never logs file contents. Rotates at 1000 lines."""
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts":      datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "action":  action,
        "file":    str(file_path),
        "outcome": outcome,
        "detail":  detail[:200],
    }
    line = json.dumps(entry, ensure_ascii=False)

    # Rotate: keep last 999 lines, append new one
    existing: list[str] = []
    if _AUDIT_LOG.exists():
        try:
            existing = _AUDIT_LOG.read_text(encoding="utf-8").splitlines()
        except Exception:
            existing = []
    existing = existing[-999:]
    existing.append(line)
    try:
        _AUDIT_LOG.write_text("\n".join(existing) + "\n", encoding="utf-8")
    except Exception:
        pass  # Never crash the bot over a log failure


# ── Syntax check ──────────────────────────────────────────────────────────────

def _check_syntax(code: str, filename: str) -> tuple[bool, str]:
    """Returns (ok, error_msg). Compiles in memory — never writes."""
    if not filename.endswith(".py"):
        return True, ""
    try:
        compile(code, filename, "exec")
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError line {e.lineno}: {e.msg}"


# ── Token system ──────────────────────────────────────────────────────────────

def _generate_token() -> str:
    return secrets.token_hex(8)  # 16-char hex


def _make_pending(
    chat_id: int,
    action: str,
    file_path: Path,
    new_content: Optional[str] = None,
) -> str:
    token = _generate_token()
    _pending[chat_id] = {
        "token":       token,
        "action":      action,
        "file_path":   file_path,
        "new_content": new_content,
        "expires_at":  datetime.now(timezone.utc).timestamp() + _TOKEN_TTL,
    }
    return token


def _pop_pending(chat_id: int, token: str) -> Optional[dict]:
    """Return and remove pending if token matches and not expired."""
    entry = _pending.get(chat_id)
    if entry is None:
        return None
    if entry["token"] != token:
        return None
    if datetime.now(timezone.utc).timestamp() > entry["expires_at"]:
        _pending.pop(chat_id, None)
        return None
    return _pending.pop(chat_id)


# ── HTML helper (local) ───────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape < > & for Telegram HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Sub-handlers ──────────────────────────────────────────────────────────────

async def do_read(update: Update, raw_path: str) -> None:
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    try:
        path = _safe_path(raw_path)
    except ValueError as e:
        _audit(chat_id, "blocked_traversal", raw_path, "blocked", str(e))
        await update.message.reply_text(f"🚫 {_esc(str(e))}", parse_mode="HTML")
        return

    if path.suffix not in _ALLOWED_EXTENSIONS:
        _audit(chat_id, "blocked_extension", path, "blocked", path.suffix)
        await update.message.reply_text(
            f"🚫 Extension <code>{_esc(path.suffix)}</code> not allowed.",
            parse_mode="HTML",
        )
        return

    if not path.exists():
        _audit(chat_id, "read", path, "not_found")
        await update.message.reply_text(
            f"❌ File not found: <code>{_esc(str(path.relative_to(WORKSPACE_ROOT)))}</code>",
            parse_mode="HTML",
        )
        return

    size = path.stat().st_size
    if size > _MAX_FILE_SIZE:
        _audit(chat_id, "read", path, "too_large", f"{size} bytes")
        await update.message.reply_text(
            f"❌ File too large ({size:,} bytes). Max {_MAX_FILE_SIZE:,} bytes.",
        )
        return

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    rel = str(path.relative_to(WORKSPACE_ROOT))

    display = _esc(content[:3000])
    truncated = len(content) > 3000

    msg = (
        f"📄 <b>{_esc(rel)}</b> ({len(lines)} lines)\n\n"
        f"<pre><code>{display}</code></pre>"
    )
    if truncated:
        msg += f"\n<i>… truncated (showing 3000/{len(content)} chars)</i>"

    _audit(chat_id, "read", path, "ok")
    try:
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(f"[{rel}]\n\n{content[:3000]}")


async def do_write_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_path: str,
) -> None:
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    try:
        path = _safe_path(raw_path)
    except ValueError as e:
        _audit(chat_id, "blocked_traversal", raw_path, "blocked", str(e))
        await update.message.reply_text(f"🚫 {_esc(str(e))}", parse_mode="HTML")
        return

    if path.suffix not in _ALLOWED_EXTENSIONS:
        _audit(chat_id, "blocked_extension", path, "blocked", path.suffix)
        await update.message.reply_text(
            f"🚫 Extension <code>{_esc(path.suffix)}</code> not allowed.",
            parse_mode="HTML",
        )
        return

    # Extract instruction: everything in context.args after the filename
    # args[0]="write", args[1]=filename, args[2:]= instruction words
    instruction_parts = context.args[2:] if context.args and len(context.args) > 2 else []
    instruction = " ".join(instruction_parts).strip()

    if not instruction:
        await update.message.reply_text(
            "✏️ What should I do with this file?\n\n"
            "Usage: <code>/code write &lt;file&gt; &lt;instruction&gt;</code>\n"
            "Example: <code>/code write agents/code_agent.py add error handling to do_read</code>",
            parse_mode="HTML",
        )
        return

    # Read existing content (or mark as new)
    if path.exists():
        size = path.stat().st_size
        if size > _MAX_FILE_SIZE:
            await update.message.reply_text(
                f"❌ File too large to edit ({size:,} bytes). Max {_MAX_FILE_SIZE:,} bytes."
            )
            return
        existing_content = path.read_text(encoding="utf-8", errors="replace")
    else:
        existing_content = None

    await update.message.reply_text("⏳ Asking AI to draft changes…")

    # Build LLM prompt with injection-guard delimiters
    file_block = (
        "---BEGIN FILE CONTENTS---\n"
        + (existing_content if existing_content is not None else "(new file)")
        + "\n---END FILE CONTENTS---"
    )
    llm_prompt = (
        f"You are a code editor. The user wants to modify this file.\n\n"
        f"FILE: {path.name}\n"
        f"{file_block}\n\n"
        f"USER INSTRUCTION: {instruction}\n\n"
        f"Reply with ONLY the complete new file contents. "
        f"No explanation, no markdown fences."
    )

    try:
        from core.brain import ask_hybrid
        new_content, brain = ask_hybrid(llm_prompt, force="simple")
    except Exception as exc:
        _audit(chat_id, "write_request", path, "llm_error", str(exc))
        await update.message.reply_text(f"❌ LLM error: {_esc(str(exc))}", parse_mode="HTML")
        return

    # Strip accidental markdown fences (``` ... ```) that some models emit
    stripped = new_content.strip()
    if stripped.startswith("```"):
        lines_s = stripped.splitlines()
        # Remove first fence line and last ``` line
        inner = lines_s[1:] if len(lines_s) > 1 else lines_s
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        stripped = "\n".join(inner)
        new_content = stripped

    # Syntax check before offering to write
    ok, err = _check_syntax(new_content, path.name)
    if not ok:
        _audit(chat_id, "syntax_error", path, "blocked", err)
        await update.message.reply_text(
            f"❌ <b>Syntax Error — write cancelled</b>\n\n"
            f"<code>{_esc(err)}</code>\n\n"
            f"AI output had a syntax error and was not written.",
            parse_mode="HTML",
        )
        return

    # Show diff preview (first 500 chars of new content)
    rel = str(path.relative_to(WORKSPACE_ROOT))
    preview = _esc(new_content[:500])

    token = _make_pending(chat_id, "write", path, new_content)
    _audit(chat_id, "write_request", path, "pending", f"token={token[:4]}…")

    await update.message.reply_text(
        f"✏️ <b>Code Agent — Write Request</b>\n\n"
        f"📄 File: <code>{_esc(rel)}</code>\n"
        f"📝 Preview (first 500 chars):\n"
        f"<pre><code>{preview}</code></pre>\n\n"
        f"Reply: <code>/code yes {token}</code> to write\n"
        f"       <code>/code no</code> to cancel\n\n"
        f"⏱ Expires in 5 minutes",
        parse_mode="HTML",
    )


async def do_run_request(update: Update, raw_path: str) -> None:
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    try:
        path = _safe_path(raw_path)
    except ValueError as e:
        _audit(chat_id, "blocked_traversal", raw_path, "blocked", str(e))
        await update.message.reply_text(f"🚫 {_esc(str(e))}", parse_mode="HTML")
        return

    if path.suffix != ".py":
        await update.message.reply_text("❌ Only .py files can be run with /code run.")
        return

    if not path.exists():
        await update.message.reply_text(
            f"❌ File not found: <code>{_esc(str(path.relative_to(WORKSPACE_ROOT)))}</code>",
            parse_mode="HTML",
        )
        return

    rel = str(path.relative_to(WORKSPACE_ROOT))
    token = _make_pending(chat_id, "run", path)
    _audit(chat_id, "run_request", path, "pending", f"token={token[:4]}…")

    await update.message.reply_text(
        f"▶️ <b>Run Request</b>\n\n"
        f"📄 <code>{_esc(rel)}</code>\n"
        f"⚠️ This will execute the file in the bot's environment.\n\n"
        f"<code>/code yes {token}</code> — confirm run\n"
        f"<code>/code no</code> — cancel",
        parse_mode="HTML",
    )


async def do_confirm(update: Update, token: str) -> None:
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    entry = _pop_pending(chat_id, token)
    if entry is None:
        await update.message.reply_text("❌ No pending operation or token expired.")
        return

    action = entry["action"]
    path: Path = entry["file_path"]
    rel = str(path.relative_to(WORKSPACE_ROOT))

    if action == "write":
        new_content: str = entry["new_content"]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_content, encoding="utf-8")
            n_lines = len(new_content.splitlines())
            _audit(chat_id, "write_ok", path, "written", f"{n_lines} lines")
            await update.message.reply_text(
                f"✅ <b>Written:</b> <code>{_esc(rel)}</code> ({n_lines} lines)",
                parse_mode="HTML",
            )
        except Exception as exc:
            _audit(chat_id, "write_ok", path, "error", str(exc))
            await update.message.reply_text(
                f"❌ Write failed: {_esc(str(exc))}", parse_mode="HTML"
            )

    elif action == "run":
        try:
            result = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
                cwd=str(WORKSPACE_ROOT),
            )
            stdout = result.stdout[:_MAX_OUTPUT]
            stderr = result.stderr[:_MAX_OUTPUT]
            rc = result.returncode
            outcome = "run_ok" if rc == 0 else "run_error"
            _audit(chat_id, "run_ok", path, outcome, f"rc={rc}")

            output_block = stdout or stderr or "(no output)"
            if len(stdout) == _MAX_OUTPUT or len(stderr) == _MAX_OUTPUT:
                output_block += "\n… (truncated)"

            status_icon = "✅" if rc == 0 else "❌"
            await update.message.reply_text(
                f"{status_icon} <b>Run:</b> <code>{_esc(rel)}</code> "
                f"(exit {rc})\n\n"
                f"<pre><code>{_esc(output_block)}</code></pre>",
                parse_mode="HTML",
            )
        except subprocess.TimeoutExpired:
            _audit(chat_id, "run_ok", path, "timeout")
            await update.message.reply_text(
                f"⏱ <b>Timeout:</b> <code>{_esc(rel)}</code> exceeded 30 s.",
                parse_mode="HTML",
            )
        except Exception as exc:
            _audit(chat_id, "run_ok", path, "run_error", str(exc))
            await update.message.reply_text(
                f"❌ Run failed: {_esc(str(exc))}", parse_mode="HTML"
            )


async def do_cancel(update: Update) -> None:
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return
    had = _pending.pop(chat_id, None)
    if had:
        _audit(chat_id, "cancelled", had.get("file_path", "?"), "cancelled")
    await update.message.reply_text("❌ Operation cancelled.")


async def do_ls(update: Update, raw_dir: str) -> None:
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    try:
        dirpath = _safe_path(raw_dir)
    except ValueError as e:
        _audit(chat_id, "blocked_traversal", raw_dir, "blocked", str(e))
        await update.message.reply_text(f"🚫 {_esc(str(e))}", parse_mode="HTML")
        return

    if not dirpath.exists():
        await update.message.reply_text(
            f"❌ Directory not found: <code>{_esc(raw_dir)}</code>", parse_mode="HTML"
        )
        return

    if not dirpath.is_dir():
        await update.message.reply_text(
            f"❌ Not a directory: <code>{_esc(raw_dir)}</code>", parse_mode="HTML"
        )
        return

    entries = sorted(dirpath.iterdir(), key=lambda p: (p.is_file(), p.name))
    entries = entries[:50]

    lines = []
    for entry in entries:
        try:
            stat = entry.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%m-%d %H:%M")
            kind = "/" if entry.is_dir() else ""
            size_str = f"{size:>8,}" if entry.is_file() else "      dir"
            lines.append(f"{mtime}  {size_str}  {entry.name}{kind}")
        except Exception:
            lines.append(f"???          {entry.name}")

    rel = str(dirpath.relative_to(WORKSPACE_ROOT)) if dirpath != WORKSPACE_ROOT else "."
    header = f"📁 {rel}/ ({len(entries)} entries)"
    body = "\n".join(lines) if lines else "(empty)"

    await update.message.reply_text(
        f"{header}\n\n<pre><code>{_esc(body)}</code></pre>",
        parse_mode="HTML",
    )


async def do_diff(update: Update, raw_path: str) -> None:
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    try:
        path = _safe_path(raw_path)
    except ValueError as e:
        _audit(chat_id, "blocked_traversal", raw_path, "blocked", str(e))
        await update.message.reply_text(f"🚫 {_esc(str(e))}", parse_mode="HTML")
        return

    # Compute path relative to WORKSPACE_ROOT for git
    try:
        rel_path = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel_path = path

    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", str(rel_path)],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            cwd=str(WORKSPACE_ROOT),
        )
        diff_out = result.stdout or result.stderr or "(no diff)"
        # Show last 50 lines
        diff_lines = diff_out.splitlines()[-50:]
        diff_text = "\n".join(diff_lines)
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱ git diff timed out.")
        return
    except FileNotFoundError:
        await update.message.reply_text("❌ git not found in PATH.")
        return
    except Exception as exc:
        await update.message.reply_text(f"❌ git diff failed: {_esc(str(exc))}", parse_mode="HTML")
        return

    rel_str = str(rel_path)
    await update.message.reply_text(
        f"🔀 <b>git diff HEAD — {_esc(rel_str)}</b>\n\n"
        f"<pre><code>{_esc(diff_text)}</code></pre>",
        parse_mode="HTML",
    )


# ── Help text ─────────────────────────────────────────────────────────────────

_HELP = (
    "🤖 <b>Code Agent</b>\n\n"
    "<code>/code read &lt;file&gt;</code> — show file contents\n"
    "<code>/code write &lt;file&gt; &lt;instruction&gt;</code> — AI edits file\n"
    "<code>/code run &lt;file&gt;</code> — execute .py file\n"
    "<code>/code ls [dir]</code> — list directory (default: root)\n"
    "<code>/code diff &lt;file&gt;</code> — git diff for file\n"
    "<code>/code yes &lt;token&gt;</code> — confirm pending write/run\n"
    "<code>/code no</code> — cancel pending operation\n\n"
    "<i>All file paths are sandboxed to the workspace root.</i>"
)


# ── Main command handler ───────────────────────────────────────────────────────

async def cmd_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main /code command dispatcher."""
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    args = context.args or []

    if not args:
        await update.message.reply_text(_HELP, parse_mode="HTML")
        return

    sub = args[0].lower()

    if sub == "read":
        if len(args) < 2:
            await update.message.reply_text("Usage: <code>/code read &lt;file&gt;</code>", parse_mode="HTML")
            return
        await do_read(update, args[1])

    elif sub == "write":
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: <code>/code write &lt;file&gt; &lt;instruction&gt;</code>", parse_mode="HTML"
            )
            return
        await do_write_request(update, context, args[1])

    elif sub == "run":
        if len(args) < 2:
            await update.message.reply_text("Usage: <code>/code run &lt;file&gt;</code>", parse_mode="HTML")
            return
        await do_run_request(update, args[1])

    elif sub == "ls":
        raw_dir = args[1] if len(args) >= 2 else "."
        await do_ls(update, raw_dir)

    elif sub == "diff":
        if len(args) < 2:
            await update.message.reply_text("Usage: <code>/code diff &lt;file&gt;</code>", parse_mode="HTML")
            return
        await do_diff(update, args[1])

    elif sub == "yes":
        if len(args) < 2:
            await update.message.reply_text("Usage: <code>/code yes &lt;token&gt;</code>", parse_mode="HTML")
            return
        await do_confirm(update, args[1])

    elif sub == "no":
        await do_cancel(update)

    else:
        await update.message.reply_text(
            f"❓ Unknown subcommand: <code>{_esc(sub)}</code>\n\n{_HELP}",
            parse_mode="HTML",
        )
