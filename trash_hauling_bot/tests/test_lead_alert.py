import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.lead_alert import DEFAULT_MAX_CHARS, build_digest


def _lead(i, urgency=5, job="junk removal", location="Dallas"):
    return {
        "id": f"lead{i:04d}",
        "urgency_score": urgency,
        "job_type": job,
        "location": location,
    }


class TestBuildDigest:
    def test_empty_leads(self):
        assert "no new leads" in build_digest([]).lower()
        assert "no new leads" in build_digest(None).lower()

    def test_single_lead_singular_wording(self):
        out = build_digest([_lead(1)])
        assert "1 new lead" in out
        assert "1 new leads" not in out

    def test_includes_lead_details(self):
        out = build_digest([_lead(1, job="cleanout", location="Plano")])
        assert "lead0001" in out
        assert "cleanout" in out
        assert "Plano" in out

    def test_ranks_by_urgency_desc(self):
        leads = [_lead(1, urgency=2), _lead(2, urgency=9), _lead(3, urgency=5)]
        out = build_digest(leads)
        assert out.index("lead0002") < out.index("lead0003") < out.index("lead0001")

    def test_caps_number_of_lines(self):
        leads = [_lead(i) for i in range(50)]
        out = build_digest(leads, max_leads=5)
        assert out.count("•") == 5
        assert "+45 more" in out

    def test_never_exceeds_max_chars(self):
        # Pathological: many leads with long fields.
        leads = [_lead(i, job="x" * 200, location="y" * 200) for i in range(500)]
        out = build_digest(leads, max_chars=600)
        assert len(out) <= 600

    def test_default_bound_is_well_under_telegram_limit(self):
        leads = [_lead(i) for i in range(1000)]
        out = build_digest(leads)
        assert len(out) <= DEFAULT_MAX_CHARS
        assert DEFAULT_MAX_CHARS < 4096

    def test_more_footer_counts_all_unshown(self):
        leads = [_lead(i) for i in range(12)]
        out = build_digest(leads, max_leads=8)
        assert "+4 more" in out

    def test_handles_non_numeric_urgency(self):
        leads = [_lead(1, urgency="high"), _lead(2, urgency=7)]
        out = build_digest(leads)
        # Should not raise and the numeric one should rank first.
        assert out.index("lead0002") < out.index("lead0001")
