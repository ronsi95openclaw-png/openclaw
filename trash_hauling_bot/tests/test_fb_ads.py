import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config
from integrations import fb_ads


class TestPayloadBuilders:
    def test_campaign_is_paused_lead_objective(self):
        c = fb_ads.build_campaign()
        assert c["status"] == "PAUSED"
        assert c["objective"] == "OUTCOME_LEADS"
        assert c["special_ad_categories"] == []

    def test_ad_set_budget_converted_to_cents(self):
        a = fb_ads.build_ad_set("c1", daily_budget=10.0, location="Town", radius_miles=25)
        assert a["daily_budget"] == 1000
        assert a["campaign_id"] == "c1"
        assert a["status"] == "PAUSED"

    def test_ad_set_enforces_min_budget(self):
        a = fb_ads.build_ad_set("c1", daily_budget=0.0, location="Town", radius_miles=25)
        # Min daily budget is $1.00 -> 100 cents.
        assert a["daily_budget"] == 100

    def test_ad_set_targets_radius(self):
        a = fb_ads.build_ad_set("c1", daily_budget=5, location="Springfield", radius_miles=40)
        loc = a["targeting"]["geo_locations"]["custom_locations"][0]
        assert loc["radius"] == 40
        assert loc["distance_unit"] == "mile"
        assert loc["name"] == "Springfield"

    def test_creative_uses_page_and_copy(self, monkeypatch):
        monkeypatch.setattr(config, "fb_page_id", "PAGE123", raising=False)
        cr = fb_ads.build_creative("Haul it away!", "https://img/x.jpg")
        spec = cr["object_story_spec"]
        assert spec["page_id"] == "PAGE123"
        assert spec["link_data"]["message"] == "Haul it away!"
        assert spec["link_data"]["picture"] == "https://img/x.jpg"

    def test_ad_links_adset_and_creative(self):
        ad = fb_ads.build_ad("AS1", "CR1")
        assert ad["adset_id"] == "AS1"
        assert ad["creative"]["creative_id"] == "CR1"
        assert ad["status"] == "PAUSED"

    def test_default_copy_mentions_f150(self):
        assert "F150" in fb_ads.DEFAULT_AD_COPY


class TestPostServiceAdDryRun:
    def test_dry_run_makes_no_network_call_and_prepares_all_stages(self):
        result = fb_ads.post_service_ad(
            daily_budget=12.0,
            location="Testville",
            radius_miles=30,
            dry_run=True,
        )
        assert result["dry_run"] is True
        stages = result["stages"]
        assert [s["stage"] for s in stages] == ["campaign", "ad_set", "creative", "ad"]
        for s in stages:
            assert s["result"]["dry_run"] is True
            assert "payload" in s["result"]

    def test_dry_run_uses_default_copy_when_none(self):
        result = fb_ads.post_service_ad(dry_run=True)
        creative_stage = next(s for s in result["stages"] if s["stage"] == "creative")
        msg = creative_stage["result"]["payload"]["object_story_spec"]["link_data"]["message"]
        assert msg == fb_ads.DEFAULT_AD_COPY

    def test_missing_creds_forces_dry_run_even_if_not_requested(self, monkeypatch):
        monkeypatch.setattr(config, "dry_run", False, raising=False)
        monkeypatch.setattr(config, "fb_ad_account_id", "", raising=False)
        monkeypatch.setattr(config, "fb_page_id", "", raising=False)
        monkeypatch.setattr(config, "fb_access_token", "", raising=False)
        result = fb_ads.post_service_ad(dry_run=False)
        # No credentials -> every stage must be a no-spend prepared payload.
        assert result["dry_run"] is True
        for s in result["stages"]:
            assert s["result"]["dry_run"] is True
            assert s["result"]["reason"] == "missing_credentials"

    def test_budget_floor_reflected_in_summary(self):
        result = fb_ads.post_service_ad(daily_budget=0.5, dry_run=True)
        assert result["daily_budget_usd"] == 1.0

    def test_summary_marks_dry_run(self):
        result = fb_ads.post_service_ad(dry_run=True)
        summary = fb_ads.summarize_for_telegram(result)
        assert "DRY-RUN" in summary
        assert "No live API call" in summary
