import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.marketing import (
    BUSINESS_NAME,
    DFW_CITIES,
    carousel_cards,
    container_pitch,
    meta_ad_copy,
    outreach_message,
    value_props,
)


class TestOutreachMessage:
    def test_mentions_business_and_job_type(self):
        msg = outreach_message(job_type="garage cleanout")
        assert BUSINESS_NAME in msg
        assert "garage cleanout" in msg

    def test_localizes_known_dfw_city(self):
        msg = outreach_message(city="Plano")
        assert "Plano" in msg

    def test_falls_back_to_metro_for_unknown_city(self):
        msg = outreach_message(city="Tulsa")
        assert "Tulsa" not in msg
        assert "DFW" in msg

    def test_container_line_toggle(self):
        with_container = outreach_message(include_container=True)
        without = outreach_message(include_container=False)
        assert "container" in with_container.lower()
        assert "container" not in without.lower()

    def test_handles_empty_job_type(self):
        msg = outreach_message(job_type="")
        assert "junk removal" in msg

    def test_offers_free_quote(self):
        assert "free quote" in outreach_message().lower()


class TestContainerPitch:
    def test_describes_drop_and_pickup(self):
        pitch = container_pitch().lower()
        assert "drop" in pitch and "pick" in pitch

    def test_localizes_city(self):
        assert "Frisco" in container_pitch(city="Frisco")


class TestMetaAdCopy:
    def test_returns_multiple_variants(self):
        ads = meta_ad_copy()
        assert len(ads) >= 3

    def test_each_ad_has_required_fields(self):
        for ad in meta_ad_copy():
            for field in ("name", "primary_text", "headline", "description", "cta"):
                assert ad.get(field), f"missing {field}"

    def test_headlines_within_meta_limit(self):
        # Meta headlines should stay short; 40 chars is the practical ceiling.
        for ad in meta_ad_copy():
            assert len(ad["headline"]) <= 40

    def test_mentions_dfw_somewhere(self):
        blob = " ".join(ad["primary_text"] for ad in meta_ad_copy()).lower()
        assert "dfw" in blob


class TestCarouselCards:
    def test_has_five_cards(self):
        assert len(carousel_cards()) == 5

    def test_each_card_has_fields(self):
        for card in carousel_cards():
            for field in ("headline", "body", "image_hint"):
                assert card.get(field), f"missing {field}"

    def test_card_headlines_within_limit(self):
        for card in carousel_cards():
            assert len(card["headline"]) <= 40


class TestValuePropsAndCities:
    def test_value_props_nonempty(self):
        assert len(value_props()) >= 3

    def test_dfw_includes_core_cities(self):
        for city in ("Dallas", "Fort Worth", "Arlington"):
            assert city in DFW_CITIES
