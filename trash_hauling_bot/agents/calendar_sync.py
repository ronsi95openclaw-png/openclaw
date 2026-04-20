"""
Sub-Agent 3 — Google Calendar Sync Agent

Monitors the Leads sheet for rows with status='scheduled' that have no
calendar_event_id yet, creates Google Calendar events for them, and writes
the event ID back. Also exposes schedule/reschedule/cancel helpers that
the Telegram bot calls directly.
"""

import logging
from typing import Optional

from integrations.gcal import CalendarClient
from integrations.sheets import (
    SheetsClient,
    STATUS_CANCELLED,
    STATUS_SCHEDULED,
)
from utils.audit import AuditLogger

logger = logging.getLogger(__name__)


class CalendarSyncAgent:
    AGENT_NAME = "calendar_sync"

    def __init__(self, audit: AuditLogger):
        self._audit = audit
        self._sheets = SheetsClient()
        self._cal = CalendarClient()

    def run(self) -> int:
        """Create calendar events for any scheduled leads that don't have one yet."""
        logger.info("Calendar sync started")
        self._audit.log(self.AGENT_NAME, "sync_started")
        created = 0

        for job in self._sheets.get_scheduled_jobs():
            if job.get("calendar_event_id"):
                continue
            if not job.get("scheduled_datetime"):
                continue
            try:
                event_id = self._cal.create_event(job)
                self._sheets.update_lead(job["id"], {"calendar_event_id": event_id})
                created += 1
                self._audit.log(self.AGENT_NAME, "event_created", {
                    "lead_id": job["id"], "event_id": event_id,
                })
            except Exception as exc:
                logger.error("Event creation failed for lead %s: %s", job.get("id"), exc)
                self._audit.log(self.AGENT_NAME, "event_error", {
                    "lead_id": job.get("id"), "error": str(exc),
                })

        self._audit.log(self.AGENT_NAME, "sync_completed", {"events_created": created})
        return created

    def schedule_job(self, lead_id: str, scheduled_dt: str, team_member: str = "") -> Optional[str]:
        """Mark a lead as scheduled, create its calendar event, and return the event ID."""
        lead = self._sheets.get_lead_by_id(lead_id)
        if not lead:
            return None

        updates = {"status": STATUS_SCHEDULED, "scheduled_datetime": scheduled_dt}
        if team_member:
            updates["assigned_team_member"] = team_member
        self._sheets.update_lead(lead_id, updates)
        lead.update(updates)

        try:
            event_id = self._cal.create_event(lead)
            self._sheets.update_lead(lead_id, {"calendar_event_id": event_id})
            self._audit.log(self.AGENT_NAME, "job_scheduled", {
                "lead_id": lead_id, "event_id": event_id, "datetime": scheduled_dt,
            })
            return event_id
        except Exception as exc:
            logger.error("Schedule job failed for %s: %s", lead_id, exc)
            self._audit.log(self.AGENT_NAME, "schedule_error", {"lead_id": lead_id, "error": str(exc)})
            return None

    def reschedule_job(self, lead_id: str, new_dt: str) -> bool:
        lead = self._sheets.get_lead_by_id(lead_id)
        if not lead or not lead.get("calendar_event_id"):
            return False
        ok = self._cal.update_event(lead["calendar_event_id"], {"scheduled_datetime": new_dt})
        if ok:
            self._sheets.update_lead(lead_id, {"scheduled_datetime": new_dt})
            self._audit.log(self.AGENT_NAME, "job_rescheduled", {"lead_id": lead_id, "new_dt": new_dt})
        return ok

    def cancel_job(self, lead_id: str) -> bool:
        lead = self._sheets.get_lead_by_id(lead_id)
        if not lead:
            return False
        if lead.get("calendar_event_id"):
            self._cal.delete_event(lead["calendar_event_id"])
        self._sheets.update_lead(lead_id, {"status": STATUS_CANCELLED, "calendar_event_id": ""})
        self._audit.log(self.AGENT_NAME, "job_cancelled", {"lead_id": lead_id})
        return True
