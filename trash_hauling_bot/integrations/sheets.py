import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import config
from utils.retry import with_retry
from utils.sanitize import sanitize_lead_field

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COLUMNS = [
    "id", "name", "contact", "listing_url", "job_type", "description",
    "location", "urgency_score", "size_score", "date_found", "status",
    "assigned_team_member", "outreach_message", "outreach_queued_at",
    "outreach_sent_at", "response_notes", "scheduled_datetime",
    "calendar_event_id", "last_updated",
]

STATUS_NEW = "new"
STATUS_OUTREACH_QUEUED = "outreach_queued"
STATUS_OUTREACH_SENT = "outreach_sent"
STATUS_RESPONDED = "responded"
STATUS_SCHEDULED = "scheduled"
STATUS_COMPLETED = "completed"
STATUS_DECLINED = "declined"
STATUS_NO_RESPONSE = "no_response"
STATUS_CANCELLED = "cancelled"

_STALE_STATUSES = {STATUS_NEW, STATUS_OUTREACH_SENT}


def _build_row(lead_id: str, lead: Dict, now: str) -> List[Any]:
    return [
        lead_id,
        sanitize_lead_field(lead.get("name", ""), "name"),
        sanitize_lead_field(lead.get("contact", ""), "contact"),
        lead.get("listing_url", ""),
        sanitize_lead_field(lead.get("job_type", ""), "job_type"),
        sanitize_lead_field(lead.get("description", ""), "description", max_length=800),
        sanitize_lead_field(lead.get("location", ""), "location"),
        str(lead.get("urgency_score", 1)),
        str(lead.get("size_score", 1)),
        now,
        STATUS_NEW,
        "",  # assigned_team_member
        "",  # outreach_message
        "",  # outreach_queued_at
        "",  # outreach_sent_at
        "",  # response_notes
        "",  # scheduled_datetime
        "",  # calendar_event_id
        now, # last_updated
    ]


class SheetsClient:
    def __init__(self):
        self._dry_run = config.dry_run
        if self._dry_run:
            self._local: List[Dict] = []
            logger.info("SheetsClient: DRY-RUN mode — using in-memory store")
            return
        creds = Credentials.from_service_account_file(config.google_credentials_file, scopes=_SCOPES)
        self._gc = gspread.authorize(creds)
        self._ws: Optional[gspread.Worksheet] = None

    # ------------------------------------------------------------------ #
    # Internal: Google Sheets access                                       #
    # ------------------------------------------------------------------ #

    def _worksheet(self) -> gspread.Worksheet:
        if self._ws is None:
            spreadsheet = self._gc.open_by_key(config.google_sheet_id)
            try:
                self._ws = spreadsheet.worksheet("Leads")
            except gspread.WorksheetNotFound:
                self._ws = spreadsheet.add_worksheet("Leads", rows=1000, cols=len(COLUMNS))
                self._ws.append_row(COLUMNS)
        return self._ws

    # ------------------------------------------------------------------ #
    # Public API (identical interface in both live and dry-run modes)      #
    # ------------------------------------------------------------------ #

    def add_lead(self, lead: Dict) -> str:
        lead_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        if self._dry_run:
            record = dict(zip(COLUMNS, _build_row(lead_id, lead, now)))
            self._local.append(record)
            logger.info("[DRY-RUN] Lead added: %s", lead_id)
            return lead_id
        return self._remote_add(lead_id, lead, now)

    @with_retry(max_attempts=3, delay=1.5)
    def _remote_add(self, lead_id: str, lead: Dict, now: str) -> str:
        self._worksheet().append_row(_build_row(lead_id, lead, now), value_input_option="USER_ENTERED")
        logger.info("Lead added: %s", lead_id)
        return lead_id

    def get_leads_by_status(self, status: str) -> List[Dict]:
        if self._dry_run:
            return [r for r in self._local if r.get("status") == status]
        return self._remote_get_by_status(status)

    @with_retry(max_attempts=3, delay=1.5)
    def _remote_get_by_status(self, status: str) -> List[Dict]:
        return [r for r in self._worksheet().get_all_records() if r.get("status") == status]

    def get_lead_by_id(self, lead_id: str) -> Optional[Dict]:
        if self._dry_run:
            return next((r for r in self._local if r.get("id") == lead_id), None)
        return self._remote_get_by_id(lead_id)

    @with_retry(max_attempts=3, delay=1.5)
    def _remote_get_by_id(self, lead_id: str) -> Optional[Dict]:
        for row in self._worksheet().get_all_records():
            if row.get("id") == lead_id:
                return row
        return None

    def update_lead(self, lead_id: str, updates: Dict) -> bool:
        updates = {**updates, "last_updated": datetime.now(timezone.utc).isoformat()}
        if self._dry_run:
            for record in self._local:
                if record.get("id") == lead_id:
                    record.update(updates)
                    return True
            return False
        return self._remote_update(lead_id, updates)

    @with_retry(max_attempts=3, delay=1.5)
    def _remote_update(self, lead_id: str, updates: Dict) -> bool:
        ws = self._worksheet()
        records = ws.get_all_records()
        for i, row in enumerate(records, start=2):  # row 1 is header
            if row.get("id") == lead_id:
                for col, value in updates.items():
                    if col in COLUMNS:
                        ws.update_cell(i, COLUMNS.index(col) + 1, str(value) if value is not None else "")
                return True
        return False

    def get_scheduled_jobs(self) -> List[Dict]:
        return self.get_leads_by_status(STATUS_SCHEDULED)

    def get_all_leads(self, limit: int = 20) -> List[Dict]:
        if self._dry_run:
            data = self._local
            return data[-limit:] if len(data) > limit else list(data)
        return self._remote_get_all(limit)

    @with_retry(max_attempts=3, delay=1.5)
    def _remote_get_all(self, limit: int) -> List[Dict]:
        records = self._worksheet().get_all_records()
        return records[-limit:] if len(records) > limit else records

    def mark_stale_leads(self, stale_days: int = 7) -> int:
        """Mark leads with no activity for stale_days as no_response. Returns count changed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        if self._dry_run:
            return self._age_local(cutoff)
        return self._remote_age(cutoff)

    def _age_local(self, cutoff: datetime) -> int:
        count = 0
        now_str = datetime.now(timezone.utc).isoformat()
        for record in self._local:
            if record.get("status") not in _STALE_STATUSES:
                continue
            try:
                updated = datetime.fromisoformat(record.get("last_updated", ""))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if updated < cutoff:
                    record["status"] = STATUS_NO_RESPONSE
                    record["last_updated"] = now_str
                    count += 1
            except (ValueError, TypeError):
                pass
        if count:
            logger.info("[DRY-RUN] Aged %d stale leads to no_response", count)
        return count

    @with_retry(max_attempts=3, delay=1.5)
    def _remote_age(self, cutoff: datetime) -> int:
        ws = self._worksheet()
        records = ws.get_all_records()
        count = 0
        now_str = datetime.now(timezone.utc).isoformat()
        status_col = COLUMNS.index("status") + 1
        updated_col = COLUMNS.index("last_updated") + 1

        for i, row in enumerate(records, start=2):
            if row.get("status") not in _STALE_STATUSES:
                continue
            try:
                updated = datetime.fromisoformat(row.get("last_updated", ""))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if updated < cutoff:
                    ws.update_cell(i, status_col, STATUS_NO_RESPONSE)
                    ws.update_cell(i, updated_col, now_str)
                    count += 1
            except (ValueError, TypeError):
                pass
        return count
