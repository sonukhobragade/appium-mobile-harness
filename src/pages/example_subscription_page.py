"""Example page object.

Not used by any test in this repository. It exists so the shape is visible
before you write your own: locators as class constants, one method per thing a
test wants to do, and nothing else.

Waits, retries and scroll-into-view live in BasePage. If any of those appear in
a page object, they belong one level down instead.
"""

from src.pages.base_page import BasePage


class ExampleSubscriptionPage(BasePage):
    # Locators. Prefer stable testIDs over text or xpath: text changes with
    # copy edits and localisation, xpath changes with layout.
    PLAN_NAME = "subscription_plan_name"
    PLAN_PRICE = "subscription_plan_price"
    SUBSCRIBE_BUTTON = "subscription_subscribe_button"
    CONFIRMATION_BANNER = "subscription_confirmation_banner"

    def plan_name(self) -> str:
        """Text of the currently displayed plan name."""
        return self.find_element(*self._by_id(self.PLAN_NAME)).text

    def plan_price(self) -> str:
        """Text of the currently displayed price, without currency symbol."""
        return self.find_element(*self._by_id(self.PLAN_PRICE)).text

    def subscribe(self) -> None:
        """Tap subscribe. Does not wait for the result; let the test assert."""
        self.find_element(*self._by_id(self.SUBSCRIBE_BUTTON)).click()

    def is_confirmed(self) -> bool:
        """Whether the confirmation banner is visible.

        Note what this does and does not tell you. A True here means the app
        rendered a banner. Whether a subscription exists in the backend is a
        separate question, and answering it is what the oracles are for.
        """
        return self.is_displayed(*self._by_id(self.CONFIRMATION_BANNER))
