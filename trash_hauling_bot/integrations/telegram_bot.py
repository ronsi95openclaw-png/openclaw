import asyncio
import functools
import logging
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agents.quote import estimate
from agents.review import review_request_message

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import config
from integrations.sheets import SheetsClient
from utils.audit import AuditLogger
from utils.sanitize import sanitize_text

if TYPE_CHECKING:
    from agents.scraper import ScraperAgent
    from agents.outreach import OutreachAgent
    from agents.calendar_sync import CalendarSyncAgent

logger = logging.getLogger(__name__)

_HELP = """
*Trash Hauling Bot — Commands*

*Leads*
/leads `[status]` — List leads (default: new)
/lead `<id>` — Show lead details
/scan — Trigger a live FB Marketplace scan

*Outreach*
/outreach `<lead_id>` — Generate & queue outreach for a lead
/pending — Show all pending confirmation requests
/confirm `<queue_id>` — Approve outreach (marks as sent)
/deny `<queue_id>` — Decline outreach (marks lead declined)

*Scheduling*
/schedule `<lead_id> <datetime> [team]` — Schedule a job
  Example: /schedule abc12345 2024-06-15T09:00 John
/jobs — List scheduled jobs
/reschedule `<lead_id> <new_datetime>` — Move a job
/cancel `<lead_id>` — Cancel a job

*Reviews*
/review `<lead_id>` — Generate a post-job Google review request message (for team to copy-send)
/quote `<description>` — Get a quick price estimate for a job description

*Queue*
/clearqueue — Wipe all pending outreach items (use if stale)

*System*
/sync — Trigger calendar sync
/status — Show lead counts and system state
/topleads — Top 5 highest-scored new leads
/help — Show this message
"""


def _is_authorized(chat_id: int) -> bool:
    return not config.authorized_chat_ids or chat_id in config.authorized_chat_ids


def _require_auth(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Class methods receive (self, update, ctx) — find the Update by type
        update = next((a for a in args if isinstance(a, Update)), None)
        if update is None:
            return
        if not _is_authorized(update.effective_chat.id):
            await update.message.reply_text("Unauthorized.")
            return
        return await func(*args, **kwargs)
    return wrapper


class TrashHaulingBot:
    def __init__(
        self,
        scraper: "ScraperAgent",
        outreach: "OutreachAgent",
        cal_sync: "CalendarSyncAgent",
        audit: AuditLogger,
    ):
        self._scraper = scraper
        self._outreach = outreach
        self._cal_sync = cal_sync
        self._audit = audit
        self._sheets = SheetsClient()
        self._app = Application.builder().token(config.bot_token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        add = self._app.add_handler
        self._app.add_error_handler(self._on_error)
        add(CommandHandler("start", self._cmd_start))
        add(CommandHandler("help", self._cmd_help))
        add(CommandHandler("status", self._cmd_status))
        add(CommandHandler("leads", self._cmd_leads))
        add(CommandHandler("lead", self._cmd_lead))
        add(CommandHandler("outreach", self._cmd_outreach))
        add(CommandHandler("pending", self._cmd_pending))
        add(CommandHandler("confirm", self._cmd_confirm))
        add(CommandHandler("deny", self._cmd_deny))
        add(CommandHandler("schedule", self._cmd_schedule))
        add(CommandHandler("jobs", self._cmd_jobs))
        add(CommandHandler("reschedule", self._cmd_reschedule))
        add(CommandHandler("cancel", self._cmd_cancel))
        add(CommandHandler("scan", self._cmd_scan))
        add(CommandHandler("sync", self._cmd_sync))
        add(CommandHandler("ping", self._cmd_ping))
        add(CommandHandler("review", self._cmd_review))
        add(CommandHandler("quote", self._cmd_quote))
        add(CommandHandler("topleads", self._cmd_topleads))
        add(CommandHandler("clearqueue", self._cmd_clearqueue))
        add(CallbackQueryHandler(self._on_callback))

    async def _cmd_ping(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """No-auth ping to confirm the bot is alive and receiving updates."""
        cid = update.effective_chat.id
        logger.info("PING from chat_id=%s", cid)
        await update.message.reply_text(
            f"Pong! Bot is alive.\nYour chat_id: `{cid}`\nAdd it to TRASH_BOT_CHAT_IDS in the root .env",
            parse_mode="Markdown",
        )

    @_require_auth
    async def _cmd_topleads(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Top 5 new leads sorted by urgency + size score."""
        leads = self._sheets.get_leads_by_status("new")
        if not leads:
            await update.message.reply_text("No new leads.")
            return
        ranked = sorted(
            leads,
            key=lambda l: (int(l.get("urgency_score") or 0) + int(l.get("size_score") or 0)),
            reverse=True,
        )
        lines = [f"*Top Leads* (from {len(leads)} new)\n"]
        for lead in ranked[:5]:
            score = int(lead.get("urgency_score") or 0) + int(lead.get("size_score") or 0)
            lines.append(
                f"• `{lead['id']}` score={score} | {lead.get('job_type', 'N/A')} | "
                f"{lead.get('location', 'N/A')}\n"
                f"  {str(lead.get('description', ''))[:80]}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    @_require_auth
    async def _cmd_clearqueue(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Wipe all pending outreach items."""
        count = self._outreach.clear_queue()
        self._audit.log("telegram", "queue_cleared", {"count": count, "by": update.effective_chat.id})
        await update.message.reply_text(
            f"Queue cleared — {count} item(s) removed.\n"
            "Note: Sheets statuses are unchanged. Use /leads outreach_queued to review."
        )

    @_require_auth
    async def _cmd_quote(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Quick price estimate from a free-text job description."""
        if not ctx.args:
            await update.message.reply_text("Usage: /quote <job description>\nExample: /quote couch mattress and a few bags of trash")
            return
        description = " ".join(ctx.args)
        est = estimate(description)
        await update.message.reply_text(
            f"*Quote Estimate*\nDescription: _{description}_\n"
            f"Tier: {est['tier']} | Range: *{est['range']}*\n"
            f"(Base ${est['price']} — confirmed on-site)",
            parse_mode="Markdown",
        )

    @_require_auth
    async def _cmd_review(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Generate a Google review request message for a completed-job lead.

        Replies with the message text for the team to copy-send through their
        usual customer channel (FB Messenger / SMS). Does NOT auto-send.
        """
        if not ctx.args:
            await update.message.reply_text("Usage: /review <lead_id>")
            return
        lead_id = ctx.args[0].strip()
        lead = self._sheets.get_lead_by_id(lead_id)
        if lead is None:
            await update.message.reply_text(f"Lead `{lead_id}` not found.", parse_mode="Markdown")
            return
        if lead.get("status") != "completed":
            await update.message.reply_text(
                f"Lead `{lead_id}` is not completed (status: {lead.get('status', 'unknown')}). "
                "Only completed jobs can request reviews.",
                parse_mode="Markdown",
            )
            return
        msg = review_request_message(
            customer_name=lead.get("name", ""),
            review_url=config.google_review_url,
            business_name="HaulYeah",
        )
        await update.message.reply_text(
            f"*Review request draft for `{lead_id}`* — copy/send via your usual channel:\n\n{msg}",
            parse_mode="Markdown",
        )
        self._audit.log("telegram", "review_drafted", {"lead_id": lead_id})

    async def _on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Unhandled exception in handler:\n%s", traceback.format_exc())
        if isinstance(update, Update) and update.message:
            try:
                await update.message.reply_text(f"Internal error: {ctx.error}")
            except Exception:
                pass

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Trash Hauling Bot started and polling")

    async def stop(self) -> None:
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def notify_team(self, text: str, parse_mode: str = "Markdown") -> None:
        for chat_id in config.team_chat_ids:
            try:
                await self._app.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
            except Exception as exc:
                logger.error("Team notify to %s failed: %s", chat_id, exc)

    # ------------------------------------------------------------------ #
    # Command handlers                                                     #
    # ------------------------------------------------------------------ #

    @_require_auth
    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Trash Hauling Bot active. /help for commands.")

    @_require_auth
    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(_HELP, parse_mode="Markdown")

    @_require_auth
    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        counts = {s: len(self._sheets.get_leads_by_status(s))
                  for s in ("new", "outreach_queued", "outreach_sent", "scheduled")}
        pending = len(self._outreach.get_pending())
        lines = [
            "*System Status*",
            f"New leads: {counts['new']}",
            f"Outreach queued: {counts['outreach_queued']}",
            f"Outreach sent: {counts['outreach_sent']}",
            f"Pending confirmations: {pending}",
            f"Scheduled jobs: {counts['scheduled']}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    @_require_auth
    async def _cmd_leads(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        status = ctx.args[0] if ctx.args else "new"
        leads = self._sheets.get_leads_by_status(status)
        if not leads:
            await update.message.reply_text(f"No leads with status `{status}`.", parse_mode="Markdown")
            return
        shown = leads[-10:]
        header = f"*Leads — {status}* ({len(leads)} total"
        if len(leads) > 10:
            header += f", showing latest 10"
        header += ")\n"
        lines = [header]
        for lead in shown:
            lines.append(
                f"• `{lead['id']}` {lead.get('job_type', 'N/A')} | "
                f"{lead.get('location', 'N/A')} | urgency {lead.get('urgency_score', '?')}/10"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    @_require_auth
    async def _cmd_lead(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("Usage: /lead <id>")
            return
        lead = self._sheets.get_lead_by_id(sanitize_text(ctx.args[0], max_length=16))
        if not lead:
            await update.message.reply_text("Lead not found.")
            return
        await update.message.reply_text(
            f"*Lead `{lead['id']}`*\n"
            f"Type: {lead.get('job_type', 'N/A')}\n"
            f"Location: {lead.get('location', 'N/A')}\n"
            f"Contact: {lead.get('contact', 'N/A')}\n"
            f"Status: {lead.get('status', 'N/A')}\n"
            f"Urgency: {lead.get('urgency_score', '?')}/10  Size: {lead.get('size_score', '?')}/10\n"
            f"Found: {str(lead.get('date_found', ''))[:10]}\n"
            f"URL: {lead.get('listing_url', 'N/A')}",
            parse_mode="Markdown",
        )

    @_require_auth
    async def _cmd_outreach(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("Usage: /outreach <lead_id>")
            return
        lead_id = sanitize_text(ctx.args[0], max_length=16)
        lead = self._sheets.get_lead_by_id(lead_id)
        if not lead:
            await update.message.reply_text("Lead not found.")
            return
        await update.message.reply_text(f"Generating outreach for `{lead_id}`…", parse_mode="Markdown")
        queue_id = self._outreach.queue_outreach(lead)
        entry = self._outreach.get_pending_by_id(queue_id)
        await self._send_confirmation(update.effective_chat.id, entry)

    @_require_auth
    async def _cmd_pending(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        pending = self._outreach.get_pending()
        if not pending:
            await update.message.reply_text("No pending outreach confirmations.")
            return
        total = len(pending)
        shown = pending[-5:]
        if total > 5:
            await update.message.reply_text(
                f"{total} items in queue — showing last 5. Use /clearqueue to wipe stale ones."
            )
        for entry in shown:
            await self._send_confirmation(update.effective_chat.id, entry)

    @_require_auth
    async def _cmd_confirm(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("Usage: /confirm <queue_id>")
            return
        entry = self._outreach.confirm_send(sanitize_text(ctx.args[0], max_length=16))
        if not entry:
            await update.message.reply_text("Not found or already processed.")
            return
        await update.message.reply_text(
            f"Outreach confirmed for lead `{entry['lead_id']}`.\n"
            f"Use this URL to send the message: {entry.get('listing_url', 'N/A')}",
            parse_mode="Markdown",
        )

    @_require_auth
    async def _cmd_deny(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("Usage: /deny <queue_id>")
            return
        entry = self._outreach.deny(sanitize_text(ctx.args[0], max_length=16))
        if not entry:
            await update.message.reply_text("Not found or already processed.")
            return
        await update.message.reply_text(
            f"Denied. Lead `{entry['lead_id']}` marked declined.", parse_mode="Markdown"
        )

    @_require_auth
    async def _cmd_schedule(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /schedule <lead_id> <datetime> [team_member]\n"
                "Example: /schedule abc12345 2024-06-15T09:00 John"
            )
            return
        lead_id = sanitize_text(args[0], max_length=16)
        scheduled_dt = sanitize_text(args[1], max_length=32)
        team = sanitize_text(" ".join(args[2:]), max_length=64) if len(args) > 2 else ""

        try:
            datetime.fromisoformat(scheduled_dt)
        except ValueError:
            await update.message.reply_text("Invalid datetime. Use ISO format: 2024-06-15T09:00")
            return

        event_id = self._cal_sync.schedule_job(lead_id, scheduled_dt, team)
        if not event_id:
            await update.message.reply_text("Scheduling failed. Check the lead ID and try again.")
            return

        await update.message.reply_text(
            f"Job scheduled!\nLead: `{lead_id}`\nTime: {scheduled_dt}\n"
            f"Team: {team or 'unassigned'}\nCalendar event: `{event_id}`",
            parse_mode="Markdown",
        )
        await self.notify_team(
            f"New job scheduled\nLead: `{lead_id}` | {scheduled_dt} | Team: {team or 'TBD'}"
        )

    @_require_auth
    async def _cmd_jobs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        jobs = self._sheets.get_scheduled_jobs()
        if not jobs:
            await update.message.reply_text("No scheduled jobs.")
            return
        lines = [f"*Upcoming Jobs* ({len(jobs)} total)\n"]
        for job in jobs[:10]:
            lines.append(
                f"• `{job['id']}` {str(job.get('scheduled_datetime', 'TBD'))[:16]} | "
                f"{job.get('location', 'N/A')} | {job.get('assigned_team_member', 'unassigned')}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    @_require_auth
    async def _cmd_reschedule(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if len(ctx.args) < 2:
            await update.message.reply_text("Usage: /reschedule <lead_id> <new_datetime>")
            return
        lead_id = sanitize_text(ctx.args[0], max_length=16)
        new_dt = sanitize_text(ctx.args[1], max_length=32)
        ok = self._cal_sync.reschedule_job(lead_id, new_dt)
        msg = f"Job `{lead_id}` rescheduled to {new_dt}." if ok else "Reschedule failed."
        await update.message.reply_text(msg, parse_mode="Markdown")

    @_require_auth
    async def _cmd_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("Usage: /cancel <lead_id>")
            return
        lead_id = sanitize_text(ctx.args[0], max_length=16)
        ok = self._cal_sync.cancel_job(lead_id)
        msg = f"Job `{lead_id}` cancelled." if ok else "Cancel failed."
        await update.message.reply_text(msg, parse_mode="Markdown")
        if ok:
            await self.notify_team(f"Job `{lead_id}` has been cancelled.")

    @_require_auth
    async def _cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Starting FB Marketplace scan…")
        try:
            count = await self._scraper.run()
            await update.message.reply_text(f"Scan complete — {count} new lead(s) found.")
        except Exception as exc:
            logger.error("Manual scan error: %s", exc)
            await update.message.reply_text(f"Scan failed: {exc}")

    @_require_auth
    async def _cmd_sync(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Syncing calendar…")
        try:
            count = await asyncio.to_thread(self._cal_sync.run)
            await update.message.reply_text(f"Sync complete — {count} event(s) created.")
        except Exception as exc:
            await update.message.reply_text(f"Sync failed: {exc}")

    # ------------------------------------------------------------------ #
    # Inline keyboard confirmation flow                                    #
    # ------------------------------------------------------------------ #

    async def _send_confirmation(self, chat_id: int, entry: dict) -> None:
        if not entry:
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Confirm Send", callback_data=f"confirm:{entry['queue_id']}"),
            InlineKeyboardButton("Deny", callback_data=f"deny:{entry['queue_id']}"),
        ]])
        text = (
            f"*Outreach Review* — `{entry['queue_id']}`\n"
            f"Lead: `{entry['lead_id']}` | {entry.get('job_type', 'N/A')} | {entry.get('location', 'N/A')}\n"
            f"Contact: {entry.get('contact', 'N/A')}\n"
            f"Listing: {entry.get('listing_url', 'N/A')}\n\n"
            f"*Draft Message:*\n_{entry.get('message', '')}_"
        )
        await self._app.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard
        )

    async def _on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not _is_authorized(query.message.chat.id):
            return

        data = query.data or ""
        if ":" not in data:
            return
        action, queue_id = data.split(":", 1)
        queue_id = sanitize_text(queue_id, max_length=16)

        if action == "confirm":
            entry = self._outreach.confirm_send(queue_id)
            if entry:
                self._audit.log("telegram", "outreach_confirmed_via_button", {"queue_id": queue_id})
                await query.edit_message_text(
                    f"Outreach confirmed for lead `{entry['lead_id']}`.\n"
                    f"Send your message here: {entry.get('listing_url', 'N/A')}",
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text("Already processed or not found.")

        elif action == "deny":
            entry = self._outreach.deny(queue_id)
            if entry:
                self._audit.log("telegram", "outreach_denied_via_button", {"queue_id": queue_id})
                await query.edit_message_text(
                    f"Denied. Lead `{entry['lead_id']}` marked declined.", parse_mode="Markdown"
                )
            else:
                await query.edit_message_text("Already processed or not found.")
