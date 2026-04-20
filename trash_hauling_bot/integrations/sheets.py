import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import config
from utils.sanitize import sanitize_lead_field

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Column order must match the header row created by _ensure_headers()
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


class SheetsClient:
    def __init__(self):
        creds = Credentials.from_service_account_file(config.google_credentials_file, scopes=_SCOPES)
        self._gc = gspread.authorize(creds)
        self._ws: Optional[gspread.Worksheet] = None

    def _worksheet(self) -> gspread.Worksheet:
        if self._ws is None:
            spreadsheet = self._gc.open_by_key(config.google_sheet_id)
            try:
                self._ws = spreadsheet.worksheet("Leads")
            except gspread.WorksheetNotFound:
                self._ws = spreadsheet.add_worksheet("Leads", rows=1000, cols=len(COLUMNS))
                self._ws.append_row(COLUMNS)
        return self._ws

    def add_lead(self, lead: Dict) -> str:
        ws = self._worksheet()
        lead_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        row = [
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
            "",   # assigned_team_member
            "",   # outreach_message
            "",   # outreach_queued_at
            "",   # outreach_sent_at
            "",   # response_notes
            "",   # scheduled_datetime
            "",   # calendar_event_id
            now,  # last_updated
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Lead added: %s", lead_id)
        return lead_id

    def get_leads_by_status(self, status: str) -> List[Dict]:
        ws = self._worksheet()
        return [r for r in ws.get_all_records() if r.get("status") == status]

    def get_lead_by_id(self, lead_id: str) -> Optional[Dict]:
        ws = self._worksheet()
        for row in ws.get_all_records():
            if row.get("id") == lead_id:
                return row
        return None

    def update_lead(self, lead_id: str, updates: Dict) -> bool:
        ws = self._worksheet()
        records = ws.get_all_records()
        updates = {**updates, "last_updated": datetime.now(timezone.utc).isoformat()}
        for i, row in enumerate(records, start=2):  # row 1 is header
            if row.get("id") == lead_id:
                for col_name, value in updates.items():
                    if col_name in COLUMNS:
                        ws.update_cell(i, COLUMNS.index(col_name) + 1, str(value) if value is not None else "")
                return True
        return False

    def get_scheduled_jobs(self) -> List[Dict]:
        return self.get_leads_by_status(STATUS_SCHEDULED)

    def get_all_leads(self, limit: int = 20) -> List[Dict]:
        ws = self._worksheet()
        records = ws.get_all_records()
        return records[-limit:] if len(records) > limit else records
