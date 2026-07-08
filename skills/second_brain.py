from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root — two levels up from skills/
_PROJECT_ROOT = Path(__file__).parent.parent

SECOND_BRAIN_ROOT = Path.home() / "second-brain"
RAW_DIR_NAME = "raw-sources"
WIKI_DIR_NAME = "wiki"
SCHEMA_FILENAME = "CLAUDE.md"
INDEX_FILENAME = "index.md"
LOG_FILENAME = "log.md"

DEFAULT_INDEX = """# Second Brain Index

This index catalogs every wiki page in the vault.
Each entry should include a one-line summary and a link to the page.
"""

DEFAULT_LOG = """# Second Brain Log

A chronological record of ingest, query, and maintenance actions.
Write entries in the form: `YYYY-MM-DD | action | summary`.
"""

DEFAULT_SCHEMA = """# LLM Wiki Schema

This file defines how Claude should build and maintain the second-brain wiki.
It is the single source of structure, naming, and workflow rules.

## Vault structure

- `raw-sources/` — immutable source documents. The AI reads them but never edits them.
- `wiki/` — AI-generated knowledge pages. All summaries, concept pages, comparisons, and syntheses go here.
- `wiki/index.md` — catalog of all wiki pages, updated on every ingest.
- `wiki/log.md` — chronologically append-only record of every ingest, query, and health check.

## Core rules

1. **Raw source files are read-only.** Do not change them after ingestion.
2. **The AI owns `wiki/`.** When new sources arrive, create or update wiki pages and links.
3. **Use Obsidian link style** for internal references: `[[Page Name]]`.
4. **Keep pages focused.** One major topic, concept, or synthesis per file.
5. **Update the index and log on every change.** The index is the navigational map. The log is the audit trail.

## Operational workflow

### Ingest

- Read new source files from `raw-sources/`.
- Extract key ideas, entities, processes, and claims.
- Create or update relevant pages in `wiki/`.
- Add or refresh links among related pages.
- Update `wiki/index.md` with any new pages and summaries.
- Append an entry to `wiki/log.md` describing what changed.

### Query

- Search the index first for relevant pages.
- Read the selected wiki pages, not raw sources directly.
- Synthesize answers from the wiki and cite page names.
- If the answer is useful, write it back as a new wiki page.

### Health check

- Periodically scan `wiki/` for contradictions, stale claims, orphan pages, and missing connections.
- Write a health report to `wiki/lint-report.md`.
- Keep the wiki consistent as new sources accumulate.

## Page conventions

- Use clear titles with sentence-case or title-case.
- Start with a short summary paragraph (2-3 sentences).
- Use headings for structure.
- Use bullet lists for comparisons, timelines, and action items.
- Include `Sources` and `See also` sections when relevant.

## Example page structure

```md
# Topic Name

A short, high-level summary of the topic.

## Key ideas

- Idea 1
- Idea 2

## Why it matters

... brief explanation ...

## Sources

- `raw-sources/source-file.md`

## See also

- [[Related Page]]
```

## Prompt guidance

When asked to process sources, the AI should:
- treat `raw-sources/` as immutable input
- treat `wiki/` as the organized output
- preserve existing links and add new links when sources connect topics
- never overwrite the schema file automatically
- always log the action in `wiki/log.md`

## Notes

This schema is the contract between you and Claude. Update it over time as the vault grows and as your workflows evolve.
"""


def _get_root(root: Optional[Path] = None) -> Path:
    return root.resolve() if root else SECOND_BRAIN_ROOT


def _sanitize_file_name(name: str) -> str:
    cleaned = name.strip().replace(" ", "-").replace("/", "-")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in "-_.")
    return cleaned[:200] or "untitled"


def _ensure_directories(root: Path) -> None:
    (root / RAW_DIR_NAME).mkdir(parents=True, exist_ok=True)
    (root / WIKI_DIR_NAME).mkdir(parents=True, exist_ok=True)


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def initialize_second_brain(root: Optional[Path] = None) -> Dict[str, Any]:
    vault = _get_root(root)
    _ensure_directories(vault)

    schema_file = vault / SCHEMA_FILENAME
    index_file = vault / WIKI_DIR_NAME / INDEX_FILENAME
    log_file = vault / WIKI_DIR_NAME / LOG_FILENAME

    _write_if_missing(schema_file, DEFAULT_SCHEMA)
    _write_if_missing(index_file, DEFAULT_INDEX)
    _write_if_missing(log_file, DEFAULT_LOG)

    return {
        "path": str(vault),
        "raw_sources": str(vault / RAW_DIR_NAME),
        "wiki": str(vault / WIKI_DIR_NAME),
        "schema": str(schema_file),
        "index": str(index_file),
        "log": str(log_file),
    }


def get_second_brain_status(root: Optional[Path] = None) -> Dict[str, Any]:
    vault = _get_root(root)
    raw_dir = vault / RAW_DIR_NAME
    wiki_dir = vault / WIKI_DIR_NAME
    schema_file = vault / SCHEMA_FILENAME
    index_file = wiki_dir / INDEX_FILENAME
    log_file = wiki_dir / LOG_FILENAME

    return {
        "path": str(vault),
        "exists": vault.exists(),
        "raw_source_count": len(list(raw_dir.glob("*.md"))) if raw_dir.exists() else 0,
        "wiki_page_count": len([p for p in wiki_dir.glob("*.md") if p.name not in {INDEX_FILENAME, LOG_FILENAME}]) if wiki_dir.exists() else 0,
        "schema_exists": schema_file.exists(),
        "index_exists": index_file.exists(),
        "log_exists": log_file.exists(),
        "raw_dir": str(raw_dir),
        "wiki_dir": str(wiki_dir),
    }


def resolve_second_brain_file_name(arg: str) -> str:
    lower = arg.lower()
    if lower in {"index", "wiki/index"}:
        return f"{WIKI_DIR_NAME}/{INDEX_FILENAME}"
    if lower in {"log", "wiki/log"}:
        return f"{WIKI_DIR_NAME}/{LOG_FILENAME}"
    if lower in {"schema", "claude", "claude.md"}:
        return SCHEMA_FILENAME
    return arg


def get_file_preview(filename: str, root: Optional[Path] = None, lines: int = 20) -> str:
    vault = _get_root(root)
    path = vault / filename
    if not path.exists():
        raise FileNotFoundError(f"{filename} not found in second brain vault")
    content = path.read_text(encoding="utf-8").splitlines()
    preview = content[:lines]
    if len(content) > lines:
        preview.append("... (truncated)")
    return "\n".join(preview)


def append_log_entry(action: str, note: str, root: Optional[Path] = None) -> Path:
    vault = _get_root(root)
    log_file = vault / WIKI_DIR_NAME / LOG_FILENAME
    if not log_file.exists():
        initialize_second_brain(vault)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"{timestamp} | {action.strip()} | {note.strip()}\n"
    log_file.write_text(log_file.read_text(encoding="utf-8") + entry, encoding="utf-8")
    return log_file


def create_raw_source_file(title: str, content: str, root: Optional[Path] = None) -> Path:
    vault = _get_root(root)
    raw_dir = vault / RAW_DIR_NAME
    if not raw_dir.exists():
        initialize_second_brain(vault)
    name = _sanitize_file_name(title)
    path = raw_dir / f"{name}.md"
    suffix = 1
    while path.exists():
        path = raw_dir / f"{name}-{suffix}.md"
        suffix += 1
    body = f"# {title.strip()}\n\n{content.strip()}\n"
    path.write_text(body, encoding="utf-8")
    append_log_entry("new source", f"Created {path.name}", vault)
    return path


def list_raw_sources(root: Optional[Path] = None) -> List[str]:
    vault = _get_root(root)
    raw_dir = vault / RAW_DIR_NAME
    if not raw_dir.exists():
        return []
    return [str(p.name) for p in sorted(raw_dir.glob("*.md"))]


def list_wiki_pages(root: Optional[Path] = None) -> List[str]:
    vault = _get_root(root)
    wiki_dir = vault / WIKI_DIR_NAME
    if not wiki_dir.exists():
        return []
    return [str(p.name) for p in sorted(wiki_dir.glob("*.md"))]


# ── Wiki page management ───────────────────────────────────────────────────────

def create_wiki_page(title: str, content: str, root: Optional[Path] = None) -> Path:
    """Create or overwrite a wiki page. Updates index + log."""
    vault = _get_root(root)
    wiki_dir = vault / WIKI_DIR_NAME
    wiki_dir.mkdir(parents=True, exist_ok=True)
    name = _sanitize_file_name(title)
    path = wiki_dir / f"{name}.md"
    body = f"# {title.strip()}\n\n{content.strip()}\n"
    path.write_text(body, encoding="utf-8")
    _update_index(vault, path.name, title, content[:120])
    append_log_entry("wiki:create", f"Created/updated [[{path.stem}]]", vault)
    return path


def delete_wiki_page(page_name: str, root: Optional[Path] = None) -> bool:
    """Delete a wiki page by filename (with or without .md). Returns True if deleted."""
    vault = _get_root(root)
    wiki_dir = vault / WIKI_DIR_NAME
    name = page_name if page_name.endswith(".md") else f"{page_name}.md"
    path = wiki_dir / name
    if path.exists():
        path.unlink()
        append_log_entry("wiki:delete", f"Deleted [[{path.stem}]]", vault)
        return True
    return False


def _update_index(vault: Path, page_name: str, title: str, summary: str) -> None:
    """Add or refresh an entry in wiki/index.md."""
    index_file = vault / WIKI_DIR_NAME / INDEX_FILENAME
    if not index_file.exists():
        initialize_second_brain(vault)
    content = index_file.read_text(encoding="utf-8")
    stem = page_name.replace(".md", "")
    entry_line = f"- [[{stem}]] — {summary.strip()[:100]}\n"
    # Replace existing entry if present, else append
    lines = content.splitlines(keepends=True)
    new_lines = [l for l in lines if f"[[{stem}]]" not in l]
    new_lines.append(entry_line)
    index_file.write_text("".join(new_lines), encoding="utf-8")


def search_wiki(query: str, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Full-text search across all wiki pages. Returns list of {page, matches}."""
    vault = _get_root(root)
    wiki_dir = vault / WIKI_DIR_NAME
    if not wiki_dir.exists():
        return []
    results = []
    q = query.lower()
    for page in sorted(wiki_dir.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        if q in text.lower():
            # Collect matching lines
            matches = [ln.strip() for ln in text.splitlines() if q in ln.lower()][:3]
            results.append({"page": page.name, "matches": matches})
    return results


# ── LLM-powered operations ─────────────────────────────────────────────────────

def ingest_raw_sources(root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Core ingest workflow: read all raw-sources/, use LLM to generate/update
    wiki pages, update index + log. Returns summary dict.
    """
    from core.brain import ask_hybrid

    vault = _get_root(root)
    raw_dir = vault / RAW_DIR_NAME
    if not raw_dir.exists() or not list(raw_dir.glob("*.md")):
        return {"status": "no_sources", "pages_created": 0, "message": "No raw sources to ingest."}

    sources = list(raw_dir.glob("*.md"))
    pages_created = []
    errors = []

    for src in sources:
        try:
            src_text = src.read_text(encoding="utf-8")[:3000]
            prompt = (
                f"You are maintaining an Obsidian knowledge vault.\n\n"
                f"Raw source file: `{src.name}`\n\n"
                f"Content:\n{src_text}\n\n"
                f"Your task:\n"
                f"1. Extract the key ideas, entities, and insights.\n"
                f"2. Write a concise wiki page following this structure:\n"
                f"   # Title\n   Summary paragraph.\n   ## Key ideas\n   ## Why it matters\n"
                f"   ## Sources\n   ## See also\n"
                f"3. Use [[Obsidian link]] style for internal references.\n"
                f"4. Return ONLY the markdown content, no explanation.\n"
                f"First line must be: # <Title>"
            )
            wiki_content, _ = ask_hybrid(prompt, force="complex")
            # Extract title from first line
            first_line = wiki_content.strip().splitlines()[0]
            title = first_line.lstrip("#").strip() if first_line.startswith("#") else src.stem
            page_path = create_wiki_page(title, wiki_content.strip(), vault)
            pages_created.append(page_path.name)
            append_log_entry("ingest", f"Ingested {src.name} → [[{page_path.stem}]]", vault)
        except Exception as exc:
            errors.append(f"{src.name}: {exc}")

    return {
        "status": "done",
        "sources_processed": len(sources),
        "pages_created": len(pages_created),
        "pages": pages_created,
        "errors": errors,
    }


def query_second_brain(question: str, save_answer: bool = False,
                       root: Optional[Path] = None) -> str:
    """
    Query workflow: search index for relevant pages, read them, synthesize answer.
    Optionally save the answer as a new wiki page.
    """
    from core.brain import ask_hybrid

    vault = _get_root(root)
    wiki_dir = vault / WIKI_DIR_NAME

    if not wiki_dir.exists():
        return "❌ Vault not initialized. Run /secondbrain init first."

    # Step 1: gather relevant wiki page content
    all_pages = list(wiki_dir.glob("*.md"))
    context_chunks = []
    q_lower = question.lower()

    for page in all_pages:
        if page.name in {INDEX_FILENAME, LOG_FILENAME}:
            continue
        text = page.read_text(encoding="utf-8")
        if any(word in text.lower() for word in q_lower.split() if len(word) > 3):
            context_chunks.append(f"### [[{page.stem}]]\n{text[:800]}")

    if not context_chunks:
        # Fall back to using index
        index_file = wiki_dir / INDEX_FILENAME
        context_chunks = [index_file.read_text(encoding="utf-8")[:1500]] if index_file.exists() else []

    context = "\n\n".join(context_chunks[:5])  # cap at 5 pages

    prompt = (
        f"You are a second-brain knowledge assistant.\n\n"
        f"Wiki context:\n{context}\n\n"
        f"Question: {question}\n\n"
        f"Answer using only the wiki content above. Cite page names like [[PageName]]. "
        f"If information is missing, say so clearly."
    )
    answer, brain = ask_hybrid(prompt, force="complex")
    append_log_entry("query", question[:80], vault)

    if save_answer:
        title = f"Q - {question[:60]}"
        create_wiki_page(title, f"**Question:** {question}\n\n**Answer:**\n{answer}", vault)

    return f"🧠 [{brain}]\n\n{answer}"


def health_check_wiki(root: Optional[Path] = None) -> str:
    """
    Health check: LLM scans all wiki pages for contradictions, orphans, stale
    claims. Writes lint-report.md. Returns summary text.
    """
    from core.brain import ask_hybrid

    vault = _get_root(root)
    wiki_dir = vault / WIKI_DIR_NAME

    if not wiki_dir.exists():
        return "❌ Vault not initialized."

    pages = [p for p in wiki_dir.glob("*.md") if p.name not in {INDEX_FILENAME, LOG_FILENAME, "lint-report.md"}]
    if not pages:
        return "⚠️ No wiki pages found to audit."

    # Build full wiki snapshot (capped)
    wiki_dump = ""
    for p in pages:
        wiki_dump += f"\n\n=== {p.name} ===\n{p.read_text(encoding='utf-8')[:600]}"

    prompt = (
        f"You are auditing an Obsidian knowledge vault.\n\n"
        f"Wiki pages:\n{wiki_dump[:4000]}\n\n"
        f"Perform a health check. For each issue found, report:\n"
        f"- ORPHAN: page with no [[links]] to or from it\n"
        f"- CONTRADICTION: conflicting claims between pages\n"
        f"- STALE: outdated info (dates, references)\n"
        f"- MISSING LINK: mentioned concept with no page\n\n"
        f"Format as markdown. Be specific. End with a summary score (0-100)."
    )
    report, brain = ask_hybrid(prompt, force="complex")

    # Write lint-report.md
    report_path = wiki_dir / "lint-report.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_path.write_text(
        f"# Wiki Health Report\n_Generated: {ts} via {brain}_\n\n{report}\n",
        encoding="utf-8",
    )
    append_log_entry("health-check", f"Audit complete — report saved to lint-report.md", vault)

    # Return preview
    lines = report.splitlines()
    preview = "\n".join(lines[:20])
    if len(lines) > 20:
        preview += f"\n\n... ({len(lines) - 20} more lines in lint-report.md)"
    return f"🏥 <b>Wiki Health Report</b> [{brain}]\n\n{preview}"


def get_full_wiki_summary(root: Optional[Path] = None) -> str:
    """Return index.md content + list of all wiki pages for navigation."""
    vault = _get_root(root)
    wiki_dir = vault / WIKI_DIR_NAME
    raw_dir = vault / RAW_DIR_NAME

    pages = list_wiki_pages(vault)
    sources = list_raw_sources(vault)

    index_file = wiki_dir / INDEX_FILENAME
    index_preview = ""
    if index_file.exists():
        index_preview = index_file.read_text(encoding="utf-8")[:600]

    wiki_list = "\n".join(f"  • [[{p.replace('.md','')}]]" for p in pages
                          if p not in {INDEX_FILENAME, LOG_FILENAME, "lint-report.md"})
    raw_list = "\n".join(f"  • {s}" for s in sources)

    return (
        f"🗺️ <b>Second Brain Map</b>\n\n"
        f"<b>Wiki Pages ({len(pages)}):</b>\n{wiki_list or '  (none yet)'}\n\n"
        f"<b>Raw Sources ({len(sources)}):</b>\n{raw_list or '  (none yet)'}\n\n"
        f"<b>Index Preview:</b>\n<pre>{index_preview[:400]}</pre>"
    )


# ── OpenClaw system bootstrap ──────────────────────────────────────────────────

# Hardcoded project overview — injected as a raw source so the LLM knows the
# full system even before any wiki pages are generated.
_OPENCLAW_OVERVIEW = """
OpenClaw (ClawBot) is a personal trading bot + AI business partner system.
It runs on a local Ollama LLM (gemma3:4b) with Claude API fallback via ask_hybrid().

## Architecture
- content/receiver.py — Telegram bot entry point, 54 command handlers
- core/brain.py — ask_hybrid(): routes queries to Ollama or Claude API
- core/scheduler.py — APScheduler: reminders, autotrade, news, ingest jobs
- dashboard/app.py — Flask web dashboard (port 8080), 19 routes
- trading/ — Crypto.com exchange, RSI+MACD strategy, executor, backtest
- agents/ — 12 specialized agents (see Agent Registry)
- skills/ — second_brain, self_improving, agent_team_orchestrator
- security/ — whitelist auth, command audit, blocklist

## Stack
- LLM: Ollama gemma3:4b (local) + Anthropic Claude API (fallback)
- Exchange: Crypto.com (DCA + scan + autotrade)
- Telegram: python-telegram-bot >= 21.10
- Dashboard: Flask >= 3.0
- Voice: openai-whisper
- Clip pipeline: yt-dlp + ffmpeg + Whisper

## Revenue engines
1. CashClaw — Whop job scout → AI outreach → income logging
2. Clip Economy — yt-dlp download → ffmpeg split → Whisper → captions → TikTok/IG
3. Crypto Trading — RSI+MACD autotrade on Crypto.com futures

## Status (v0.9 — 2026-04-17)
- 54 Telegram commands active
- 19 Flask dashboard routes
- 8 revenue agents wired with APScheduler autopilot
- Second brain: full ingest/query/health/search capability
"""

_AGENT_REGISTRY = """
## Agent Registry — OpenClaw v0.9

| Agent | File | Purpose | State file |
|---|---|---|---|
| Job Scout | agents/job_scout.py | Scrapes Whop for freelance gigs, scores with LLM, queues for approval | data/job_scout_state.json |
| CashClaw Applier | agents/cashclaw_applier.py | Generates HumanVoice outreach for approved jobs | data/applier_state.json |
| Clip Processor | agents/clip_processor.py | yt-dlp + ffmpeg + Whisper → viral clips | data/clip_jobs.json |
| Content Pipeline | agents/content_pipeline.py | 9:16 reformat + Claude captions → queue | data/content_queue.json |
| Social Publisher | agents/social_publisher.py | TikTok/IG auto-posting on schedule | data/publish_log.json |
| Performance Tracker | agents/performance_tracker.py | Views/engagement stats + income projections | data/performance_db.json |
| Trading Agent | agents/trading_agent.py | RSI+MACD scan + DCA + Sharpe optimizer + self-coder | data/trading_agent_state.json |
| Auto Upgrade | agents/auto_upgrade.py | LLM code self-review + patch proposals | allowlist only |
| News Filter | agents/news_filter_agent.py | Macro news → BLOCK/ALLOW trading signal | every 15min |
| Code Review | agents/code_review_agent.py | Weekly full codebase audit | Sunday 09:00 UTC |
| Human Voice | agents/human_voice.py | Humanizes AI-written outreach text | via CLIPPER |
| Failure Memory | agents/failure_memory.py | Logs lessons from errors for self-improvement | data/lessons.json |

## Scheduler jobs
- news_filter: every 15min
- cashclaw_scout: every 6h
- trading_cycle: every 4h
- perf_tracker: every 6h
- stale_sweep: every 12h
- daily_publish_preview: 09:00 UTC
- secondbrain_ingest: Sunday 10:00 UTC
- code_review: Sunday 09:00 UTC
- autotrade: 08:00 UTC daily (if enabled)
"""

_COMMAND_REFERENCE = """
## Telegram Command Reference — OpenClaw v0.9

### AI / Business Partner
/ask /plan /research /clear

### Crypto & Markets
/market /scan [1h|4h|1d] /dca [coin] /news /autotrade [on|off|now|status]
/report /backtest /trades

### CashClaw Income Pipeline
/cashclaw /scout [run] /approve_job N /apply_job N /send_apply N
/discard_apply N /log_income amount source /fng

### Clip Economy Pipeline
/clip url [sec] /clips /content [path] /approve_content id [1|2|3]
/publish [now] /publishstats /tradingagent [status|cycle|optimize|dca]
/performance [snapshot]

### Knowledge & Memory
/save /notes /secondbrain [init|status|map|ingest|query|health|find|...]
/selfimprove [init|status|show|log]

### System
/status /brain /weather /run /py /remind /tasks /cancel
/upgrade /codereview /orchestrate /otasks /sweep
/restart /stop
"""

_DATA_FLOW = """
## OpenClaw Data Flow

### CashClaw pipeline (income from freelancing)
  Whop scrape → job_scout_state.json
  → /approve_job (human gate)
  → cashclaw_applier: HumanVoice outreach draft
  → /send_apply (human gate)
  → income_log.json

### Clip economy pipeline (income from content)
  /clip url → yt-dlp download → ffmpeg split → Whisper transcribe
  → clip_jobs.json
  → /content clip_path → 9:16 reformat → Claude captions
  → content_queue.json
  → /approve_content (human gate)
  → social_publisher → TikTok/IG Reels
  → publish_log.json → performance_db.json → income projections

### Trading pipeline (income from crypto)
  RSI+MACD scan (every 4h) → LLM confirmation gate
  → Crypto.com executor → trade_log.json
  → /report → performance analysis

### Self-improvement loop
  Failure → failure_memory.log_lesson()
  → selfimprove.append_correction()
  → auto_upgrade.run_auto_upgrade()
  → code patch → restart
"""


def bootstrap_openclaw(root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Seed the second brain with the entire OpenClaw system knowledge:
    - Project overview + stack
    - All wiki/*.md files from the project
    - Agent registry
    - Command reference
    - Data flow architecture
    Returns summary of what was ingested.
    """
    vault = _get_root(root)
    initialize_second_brain(vault)
    created = []

    # 1. Core project knowledge
    core_sources = {
        "OpenClaw Project Overview": _OPENCLAW_OVERVIEW,
        "OpenClaw Agent Registry": _AGENT_REGISTRY,
        "OpenClaw Command Reference": _COMMAND_REFERENCE,
        "OpenClaw Data Flow Architecture": _DATA_FLOW,
    }
    for title, content in core_sources.items():
        p = create_raw_source_file(title, content, vault)
        created.append(p.name)

    # 2. Ingest all wiki/*.md files from the project
    project_wiki = _PROJECT_ROOT / "wiki"
    if project_wiki.exists():
        for wiki_file in sorted(project_wiki.glob("*.md")):
            try:
                text = wiki_file.read_text(encoding="utf-8")
                title = wiki_file.stem.replace("-", " ").title()
                p = create_raw_source_file(f"OpenClaw Wiki - {title}", text[:4000], vault)
                created.append(p.name)
            except Exception:
                pass

    # 3. Ingest agent docstrings as source files
    agents_dir = _PROJECT_ROOT / "agents"
    if agents_dir.exists():
        for agent_file in sorted(agents_dir.glob("*.py")):
            if agent_file.name == "__init__.py":
                continue
            try:
                text = agent_file.read_text(encoding="utf-8")
                # Extract module docstring + first 60 lines
                lines = text.splitlines()[:60]
                doc = "\n".join(lines)
                title = f"Agent - {agent_file.stem.replace('_', ' ').title()}"
                p = create_raw_source_file(title, doc, vault)
                created.append(p.name)
            except Exception:
                pass

    append_log_entry("bootstrap", f"OpenClaw system sync: {len(created)} sources ingested", vault)
    return {"sources_created": len(created), "files": created}


def sync_openclaw(root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Re-sync OpenClaw project knowledge into second brain, then run LLM ingest
    to regenerate wiki pages. Full pipeline in one call.
    """
    bootstrap_result = bootstrap_openclaw(root)
    ingest_result = ingest_raw_sources(root)
    return {
        "synced": bootstrap_result["sources_created"],
        "wiki_pages": ingest_result.get("pages_created", 0),
        "pages": ingest_result.get("pages", []),
        "errors": ingest_result.get("errors", []),
    }
