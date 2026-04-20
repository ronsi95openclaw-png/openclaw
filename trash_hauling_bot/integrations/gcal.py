import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import config

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_JOB_DURATION_HOURS = 3
_DEFAULT_TIMEZONE = "America/Chicago"


class CalendarClient:
    def __init__(self):
        creds = Credentials.from_service_account_file(config.google_credentials_file, scopes=_SCOPES)
        self._svc = build("calendar", "v3", credentials=creds)
        self._cal_id = config.google_calendar_id

    def create_event(self, job: Dict) -> str:
        start_dt = self._parse_dt(job.get("scheduled_datetime", ""))
        end_dt = start_dt + timedelta(hours=_JOB_DURATION_HOURS)

        body = {
            "summary": f"Trash Haul — {job.get('job_type', 'Job')} @ {job.get('location', 'TBD')}",
            "description": "\n".join([
                f"Job Type: {job.get('job_type', 'N/A')}",
                f"Location: {job.get('location', 'N/A')}",
                f"Contact: {job.get('contact', 'N/A')}",
                f"Lead ID: {job.get('id', 'N/A')}",
                f"Assigned: {job.get('assigned_team_member', 'Unassigned')}",
                f"Description: {job.get('description', 'N/A')}",
                f"Listing: {job.get('listing_url', 'N/A')}",
            ]),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": _DEFAULT_TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": _DEFAULT_TIMEZONE},
        }
        result = self._svc.events().insert(calendarId=self._cal_id, body=body).execute()
        event_id = result.get("id", "")
        logger.info("Calendar event created: %s", event_id)
        return event_id

    def update_event(self, event_id: str, updates: Dict) -> bool:
        try:
            event = self._svc.events().get(calendarId=self._cal_id, eventId=event_id).execute()
            if "scheduled_datetime" in updates:
                start_dt = self._parse_dt(updates["scheduled_datetime"])
                end_dt = start_dt + timedelta(hours=_JOB_DURATION_HOURS)
                event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": _DEFAULT_TIMEZONE}
                event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": _DEFAULT_TIMEZONE}
            self._svc.events().update(calendarId=self._cal_id, eventId=event_id, body=event).execute()
            return True
        except Exception as exc:
            logger.error("Calendar update failed: %s", exc)
            return False

    def delete_event(self, event_id: str) -> bool:
        try:
            self._svc.events().delete(calendarId=self._cal_id, eventId=event_id).execute()
            return True
        except Exception as exc:
            logger.error("Calendar delete failed: %s", exc)
            return False

    @staticmethod
    def _parse_dt(value: str) -> datetime:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return datetime.now(timezone.utc) + timedelta(days=1)
