"""
Post-job review request — builds a short, friendly message asking a customer
to leave a Google review after a completed haul.

Pure and dependency-free. The review URL is passed in (typically from a
GOOGLE_REVIEW_URL env value) so this stays trivial to test and reuse.
"""


def review_request_message(
    customer_name: str = "",
    review_url: str = "",
    business_name: str = "HaulYeah",
) -> str:
    name = (customer_name or "").strip()
    greeting = f"Hi {name}!" if name else "Hi!"

    lines = [
        f"{greeting} Thanks for choosing {business_name}!",
        "If you have a moment, a quick Google review would mean a lot to us.",
    ]
    url = (review_url or "").strip()
    if url:
        lines.append(url)
    lines.append(f"Thanks again,\nThe {business_name} crew")

    return "\n\n".join(lines)
