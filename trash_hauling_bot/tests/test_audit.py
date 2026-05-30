import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.audit import AuditLogger


class TestAuditLogger:
    def test_creates_log_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.log")
            audit = AuditLogger(path)
            audit.log("agent", "action")
            assert os.path.exists(path)

    def test_entry_has_required_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.log")
            audit = AuditLogger(path)
            audit.log("scraper", "scan_started", {"keywords": ["junk removal"]})
            with open(path) as f:
                entry = json.loads(f.readline())
            assert entry["agent"] == "scraper"
            assert entry["action"] == "scan_started"
            assert entry["details"] == {"keywords": ["junk removal"]}
            assert "ts" in entry

    def test_empty_details_defaults_to_empty_dict(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.log")
            audit = AuditLogger(path)
            audit.log("agent", "action")
            with open(path) as f:
                entry = json.loads(f.readline())
            assert entry["details"] == {}

    def test_multiple_entries_each_on_own_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.log")
            audit = AuditLogger(path)
            for i in range(5):
                audit.log("agent", f"action_{i}")
            with open(path) as f:
                lines = [l.strip() for l in f if l.strip()]
            assert len(lines) == 5
            for line in lines:
                json.loads(line)  # every line must be valid JSON

    def test_each_entry_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.log")
            audit = AuditLogger(path)
            audit.log("outreach", "outreach_queued", {"lead_id": "abc123", "queue_id": "x1y2"})
            with open(path) as f:
                entry = json.loads(f.readline())
            assert entry["details"]["lead_id"] == "abc123"

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as d:
            nested_path = os.path.join(d, "subdir", "logs", "audit.log")
            audit = AuditLogger(nested_path)
            audit.log("agent", "action")
            assert os.path.exists(nested_path)

    def test_timestamp_is_iso_format(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.log")
            audit = AuditLogger(path)
            audit.log("agent", "action")
            with open(path) as f:
                entry = json.loads(f.readline())
            from datetime import datetime
            # Should parse without raising
            datetime.fromisoformat(entry["ts"])
