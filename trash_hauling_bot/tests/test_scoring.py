import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import private scoring helpers directly for unit testing
from agents.scraper import _score_urgency, _score_size


class TestUrgencyScoring:
    def test_baseline_score_is_one(self):
        assert _score_urgency("I need my shed cleaned out sometime") == 1

    def test_asap_raises_score(self):
        assert _score_urgency("Need this done ASAP!") > 1

    def test_today_raises_score(self):
        assert _score_urgency("Can someone come today?") > 1

    def test_urgent_raises_score(self):
        assert _score_urgency("URGENT cleanup needed") > 1

    def test_multiple_keywords_stack(self):
        score_one = _score_urgency("ASAP")
        score_two = _score_urgency("ASAP and urgent")
        assert score_two > score_one

    def test_score_capped_at_ten(self):
        very_urgent = " ".join(["asap urgent today immediately emergency quick same day"] * 5)
        assert _score_urgency(very_urgent) <= 10

    def test_case_insensitive(self):
        lower = _score_urgency("asap")
        upper = _score_urgency("ASAP")
        assert lower == upper

    def test_emergency_keyword(self):
        assert _score_urgency("emergency cleanout needed") > 1

    def test_same_day_keyword(self):
        assert _score_urgency("same day service needed") > 1


class TestSizeScoring:
    def test_full_house_is_large(self):
        assert _score_size("Full house cleanout needed") >= 8

    def test_three_bedroom_is_large(self):
        assert _score_size("3 bedroom home needs cleared") >= 7

    def test_room_is_medium(self):
        assert _score_size("One room to clear out") == 5

    def test_basement_is_medium(self):
        assert _score_size("Need basement cleaned") == 5

    def test_apartment_is_medium(self):
        assert _score_size("Small apartment cleanout") == 5

    def test_few_items_is_small(self):
        assert _score_size("Just a few items to haul away") == 3

    def test_single_item_is_small(self):
        assert _score_size("Single item pickup needed") == 3

    def test_unknown_defaults_to_four(self):
        assert _score_size("Need help with junk removal") == 4

    def test_large_beats_medium(self):
        large = _score_size("Full house 3 bedroom cleanout")
        medium = _score_size("One room")
        assert large > medium

    def test_medium_beats_small(self):
        medium = _score_size("Basement cleanout")
        small = _score_size("Few items only")
        assert medium > small
