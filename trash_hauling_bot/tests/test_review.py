import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.review import review_request_message


class TestReviewRequestMessage:
    def test_includes_customer_name_when_given(self):
        msg = review_request_message(customer_name="Ronnie")
        assert "Ronnie" in msg

    def test_no_dangling_space_when_name_missing(self):
        msg = review_request_message()
        assert "Hi !" not in msg
        assert "  " not in msg

    def test_includes_review_url_when_given(self):
        url = "https://g.page/r/example/review"
        msg = review_request_message(review_url=url)
        assert url in msg

    def test_omits_link_when_no_url(self):
        msg = review_request_message()
        assert "http" not in msg

    def test_includes_business_name(self):
        msg = review_request_message(business_name="HaulYeah")
        assert "HaulYeah" in msg

    def test_strips_whitespace_around_name(self):
        msg = review_request_message(customer_name="  Ronnie  ")
        assert "Hi Ronnie!" in msg
