import os
import sys
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.quicksend import (
    CHANNEL_MARKETPLACE,
    CHANNEL_MESSENGER,
    CHANNEL_SMS,
    build_quicksend,
)


class TestPhonePath:
    def test_builds_sms_link_from_digit_contact(self):
        result = build_quicksend("Hello there", {"contact": "4696187677"})
        assert result["channel"] == CHANNEL_SMS
        assert result["send_link"].startswith("sms:4696187677?body=")

    def test_extracts_digits_from_formatted_contact(self):
        result = build_quicksend("Hi", {"contact": "(469) 618-7677"})
        assert result["channel"] == CHANNEL_SMS
        assert result["send_link"].startswith("sms:4696187677?body=")

    def test_handles_leading_country_code(self):
        result = build_quicksend("Hi", {"contact": "+1 713-555-1234"})
        assert result["channel"] == CHANNEL_SMS
        assert result["send_link"].startswith("sms:17135551234?body=")

    def test_copy_block_is_the_message(self):
        result = build_quicksend("  Need a quote?  ", {"contact": "7135551234"})
        assert result["copy_block"] == "Need a quote?"

    def test_body_decodes_back_to_message(self):
        message = "Hi! Free quote? Text us at (469) 618-7677."
        result = build_quicksend(message, {"contact": "7135551234"})
        query = urlsplit(result["send_link"]).query
        body = parse_qs(query, keep_blank_values=True)["body"][0]
        assert unquote(body) == message


class TestNoPhoneFallback:
    def test_messenger_handle_builds_m_me_link(self):
        result = build_quicksend("Hi", {"m_me": "haulyall"})
        assert result["channel"] == CHANNEL_MESSENGER
        assert result["send_link"] == "https://m.me/haulyall"

    def test_messenger_url_is_normalized_to_handle(self):
        result = build_quicksend("Hi", {"messenger": "https://m.me/haulyall/"})
        assert result["channel"] == CHANNEL_MESSENGER
        assert result["send_link"] == "https://m.me/haulyall"

    def test_strips_at_prefix_from_handle(self):
        result = build_quicksend("Hi", {"m_me": "@haulyall"})
        assert result["send_link"] == "https://m.me/haulyall"

    def test_marketplace_thread_hint_when_only_listing_url(self):
        url = "https://www.facebook.com/marketplace/item/123456/"
        result = build_quicksend("Hi", {"listing_url": url})
        assert result["channel"] == CHANNEL_MARKETPLACE
        assert url in result["send_link"]

    def test_generic_hint_when_nothing_present(self):
        result = build_quicksend("Hi", {})
        assert result["channel"] == CHANNEL_MARKETPLACE
        assert "Messenger" in result["send_link"]

    def test_short_number_is_not_treated_as_phone(self):
        result = build_quicksend("Hi", {"contact": "911", "listing_url": "x"})
        assert result["channel"] != CHANNEL_SMS

    def test_blank_contact_falls_back(self):
        result = build_quicksend("Hi", {"contact": "", "m_me": "haulyall"})
        assert result["channel"] == CHANNEL_MESSENGER


class TestEncoding:
    def test_spaces_are_percent_encoded(self):
        result = build_quicksend("hello world", {"contact": "7135551234"})
        assert "hello%20world" in result["send_link"]

    def test_ampersand_and_question_are_escaped(self):
        message = "Free quote? Junk & debris"
        result = build_quicksend(message, {"contact": "7135551234"})
        # In the raw (still-encoded) body, '&' and '?' must be escaped so they
        # don't break URI parsing or spill into extra query params.
        raw_body = result["send_link"].split("?body=", 1)[1]
        assert "&" not in raw_body
        assert "?" not in raw_body
        # And the single decoded param round-trips to the original message.
        body = parse_qs(urlsplit(result["send_link"]).query)["body"][0]
        assert unquote(body) == message

    def test_newlines_are_encoded(self):
        result = build_quicksend("line one\nline two", {"contact": "7135551234"})
        assert "\n" not in result["send_link"]
        assert "%0A" in result["send_link"]

    def test_unicode_is_encoded(self):
        result = build_quicksend("Café cleanout — today", {"contact": "7135551234"})
        body = parse_qs(urlsplit(result["send_link"]).query)["body"][0]
        assert unquote(body) == "Café cleanout — today"
