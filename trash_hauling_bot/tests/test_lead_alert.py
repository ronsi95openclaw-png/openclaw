import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.lead_alert import format_new_leads_alert


def _lead(**overrides):
    base = {
        "description": "Full house cleanout needed ASAP",
        "location": "Springfield, IL",
        "urgency_score": 7,
        "size_score": 8,
        "listing_url": "https://www.facebook.com/marketplace/item/123/",
    }
    base.update(overrides)
    return base


class TestFormatNewLeadsAlert:
    def test_empty_list_returns_empty_string(self):
        assert format_new_leads_alert([]) == ""

    def test_single_lead_count_in_header(self):
        msg = format_new_leads_alert([_lead()])
        assert "1 new lead" in msg
        assert "1 new leads" not in msg

    def test_plural_count_in_header(self):
        msg = format_new_leads_alert([_lead(), _lead()])
        assert "2 new leads" in msg

    def test_includes_location_scores_and_url(self):
        msg = format_new_leads_alert([_lead()])
        assert "Springfield, IL" in msg
        assert "urgency 7/10" in msg
        assert "size 8/10" in msg
        assert "https://www.facebook.com/marketplace/item/123/" in msg

    def test_includes_title_from_description(self):
        msg = format_new_leads_alert([_lead(description="Garage full of junk")])
        assert "Garage full of junk" in msg

    def test_falls_back_to_job_type_when_no_description(self):
        msg = format_new_leads_alert([_lead(description="", job_type="junk removal")])
        assert "junk removal" in msg

    def test_long_title_is_truncated(self):
        long_desc = "x" * 200
        msg = format_new_leads_alert([_lead(description=long_desc)])
        assert "…" in msg
        assert "x" * 200 not in msg

    def test_missing_url_omitted_gracefully(self):
        msg = format_new_leads_alert([_lead(listing_url="")])
        assert "facebook.com/marketplace" not in msg
        assert "Springfield, IL" in msg

    def test_missing_scores_show_placeholder(self):
        lead = _lead()
        del lead["urgency_score"]
        del lead["size_score"]
        msg = format_new_leads_alert([lead])
        assert "urgency ?/10" in msg
        assert "size ?/10" in msg

    def test_overflow_summarized(self):
        leads = [_lead(description=f"Lead {i}") for i in range(13)]
        msg = format_new_leads_alert(leads)
        assert "13 new leads" in msg
        assert "and 3 more" in msg

    def test_no_overflow_line_at_limit(self):
        leads = [_lead(description=f"Lead {i}") for i in range(10)]
        msg = format_new_leads_alert(leads)
        assert "more." not in msg
