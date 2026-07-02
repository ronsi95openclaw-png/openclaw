import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.scraper import _is_demand_lead


class TestDemandLeadRelevance:
    def test_genuine_junk_removal_request_passes(self):
        passes, reason = _is_demand_lead("Need someone to haul away old furniture and junk from my garage ASAP")
        assert passes is True
        assert reason == "ok"

    def test_cleanout_request_passes(self):
        passes, reason = _is_demand_lead("Looking for a hauler to do a full house cleanout, lots of debris")
        assert passes is True

    def test_pet_rehoming_with_demand_language_is_rejected(self):
        # Real false positive: generic "need someone"/"can anyone" phrasing shows up in
        # posts that have nothing to do with junk hauling.
        passes, reason = _is_demand_lead(
            "Can anyone take this sweet cat, moving this weekend and need someone to help find her a home"
        )
        assert passes is False
        assert reason == "no_topical_signal"

    def test_generic_looking_for_without_junk_topic_is_rejected(self):
        passes, reason = _is_demand_lead("Looking for someone to help me move this weekend, willing to pay")
        assert passes is False
        assert reason == "no_topical_signal"

    def test_supply_signal_still_rejected_even_with_topic(self):
        passes, reason = _is_demand_lead(
            "Need it removed? We offer junk removal and hauling services, licensed and insured"
        )
        assert passes is False
        assert reason == "supply_signal"

    def test_selling_furniture_with_junk_words_still_rejected(self):
        passes, reason = _is_demand_lead(
            "Looking for a buyer, old couch and mattress for sale, OBO, must pick up, junk removal welcome"
        )
        assert passes is False
        assert reason == "selling_signal"

    def test_no_demand_signal_at_all_is_rejected(self):
        passes, reason = _is_demand_lead("Beautiful 3 bedroom furniture set, great condition")
        assert passes is False
        assert reason == "no_demand_signal"
