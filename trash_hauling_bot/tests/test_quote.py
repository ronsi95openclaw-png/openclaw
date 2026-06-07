import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.quote import PRICING, estimate, estimate_tier, format_quote


class TestEstimateTier:
    def test_empty_description_is_minimum(self):
        assert estimate_tier("") == "minimum"

    def test_none_description_is_minimum(self):
        assert estimate_tier(None) == "minimum"

    def test_generic_description_is_minimum(self):
        assert estimate_tier("need some junk removal help") == "minimum"

    def test_small_cues_are_quarter(self):
        assert estimate_tier("just a couple of chairs and a mattress") == "quarter"

    def test_half_cues_are_half(self):
        assert estimate_tier("about half a garage, several items") == "half"

    def test_large_cues_are_three_quarter(self):
        assert estimate_tier("a big pile of debris, large amount") == "three_quarter"

    def test_full_cues_are_full(self):
        assert estimate_tier("full truck, whole house cleanout, everything must go") == "full"

    def test_case_insensitive(self):
        assert estimate_tier("FULL TRUCK LOAD") == "full"

    def test_larger_tier_wins_over_smaller_cue(self):
        # contains both a quarter cue ("few items") and a full cue ("full truck")
        assert estimate_tier("a few items, well actually a full truck worth") == "full"

    def test_half_beats_quarter(self):
        assert estimate_tier("a couch and a few items") == "half"


class TestEstimate:
    def test_price_matches_tier(self):
        result = estimate("full truck whole house")
        assert result["tier"] == "full"
        assert result["price"] == PRICING["full"]

    def test_minimum_default_price(self):
        result = estimate("just some junk")
        assert result["tier"] == "minimum"
        assert result["price"] == 89

    def test_range_format(self):
        result = estimate("just some junk")  # minimum = 89
        assert result["range"] == "$89-$189"

    def test_full_range_is_capped_at_max(self):
        result = estimate("full truck whole house everything")  # 699
        assert result["range"] == "$699-$699"


class TestFormatQuote:
    def test_includes_price_range(self):
        msg = format_quote("just some junk")
        assert "$89-$189" in msg

    def test_includes_business_name(self):
        msg = format_quote("full truck", business_name="HaulYeah")
        assert "HaulYeah" in msg
