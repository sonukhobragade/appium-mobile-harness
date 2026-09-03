"""Example transforms module.

Copy to src/utils/transforms/__init__.py and adapt. The expected_screens
fixture imports get_all_expected from there; if the module is absent, tests
asking for that fixture skip rather than fail.

The job of this module is narrow: turn backend payloads into the text each
screen should display. It is the only place that knows both your API shape and
your UI copy, which is why it cannot ship with the harness.
"""


def get_all_expected(backend_data: dict) -> dict:
    """Build expected screen text from fetched backend data.

    Args:
        backend_data: dict keyed by endpoint name, as returned by
            fetch_all_endpoints(). Values are the response after
            response_key extraction.

    Returns:
        dict keyed by screen name; each value is a dict of expected text.
    """
    return {
        "subscription": _subscription_screen(backend_data.get("subscription_plans", [])),
    }


def _subscription_screen(plans: list) -> dict:
    """Expected values for the subscription screen.

    Formatting belongs here, not in the test. If the UI renders "₹499" and the
    API returns "499", this is where that gap is closed, once, rather than in
    every assertion.
    """
    if not plans:
        return {}

    pro = next((p for p in plans if p.get("id") == "plan_pro"), None)
    if not pro:
        return {}

    return {
        "name": pro["name"],
        "price": pro["price"],
        "price_display": f"₹{pro['price']}",
        "device_limit": str(pro["limits"]["max_devices"]),
    }
