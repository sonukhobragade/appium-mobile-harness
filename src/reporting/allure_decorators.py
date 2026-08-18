"""
Allure Decorators for Automatic Test Reporting.

This module provides decorators that automatically wrap methods with Allure steps,
eliminating the need for manual `with allure.step()` context managers in tests.

NAMING CONVENTIONS (Future-Proof):
================================

To keep your Page Object methods automatically organized in Allure reports,
follow these naming conventions:

✅ PUBLIC BUSINESS METHODS (will appear as Allure steps):
    - login(phone, otp)
    - enter_phone_number(phone)
    - tap_get_otp()
    - verify_login_success()

❌ INTERNAL HELPER METHODS (automatically excluded):
    - Methods ending with '_with_strategies': enter_otp_with_strategies()
    - Methods ending with '_with_fallbacks': tap_with_fallbacks()
    - Methods ending with '_visible': is_home_screen_visible()
    - Methods ending with '_available': is_button_available()
    - Methods starting with '_': _enter_otp_single_field()

Example:
    @allure_step_class
    class LoginPage(BasePage):
        def enter_phone_number(self, phone: str):
            # ✅ PUBLIC: Shows in report as "Enter Phone Number: 5550000000"
            return self._input_with_retry(phone)

        def _input_with_retry(self, text: str):
            # ❌ PRIVATE: Hidden from report (starts with _)
            pass

        def is_login_screen_visible(self) -> bool:
            # ❌ HELPER: Hidden from report (ends with _visible)
            pass

NO MANUAL UPDATES NEEDED: Just follow the naming conventions!
"""

import functools
from collections.abc import Callable

import allure


def allure_step_class(cls: type) -> type:
    """
    Class decorator that wraps PUBLIC PAGE-LEVEL methods with allure.step().

    This decorator automatically adds Allure step reporting to high-level business
    methods while excluding low-level infrastructure methods from BasePage.

    EXCLUSIONS (to keep reports clean):
    - Low-level BasePage infrastructure methods (find_element, tap, etc.)
    - Private methods (starting with _)
    - Magic methods (__init__, __str__, etc.)
    - Properties and class attributes

    Features:
    - Automatically generates human-readable step names from method names
    - Shows first argument in step name (masks sensitive data like OTP)
    - Only shows business-meaningful actions in Allure reports
    - Preserves method signatures and docstrings

    Args:
        cls: The class to decorate

    Returns:
        The decorated class with business methods wrapped

    Usage:
        @allure_step_class
        class LoginPage(BasePage):
            def enter_phone_number(self, phone): ...  # ✓ Will be a step
            def tap_get_otp(self): ...                # ✓ Will be a step

        # Low-level methods are automatically excluded:
        # - find_element() - ✗ Not a step (too technical)
        # - tap() - ✗ Not a step (no context)

    Example Output in Allure:
        ✓ Enter Phone Number: 5550000000
        ✓ Tap Get OTP
        ✓ Wait For OTP Screen
        (No "Find Element", "Tap", etc. noise!)
    """
    # Blacklist: Low-level infrastructure methods from BasePage
    # These are too technical and clutter the Allure report with noise
    EXCLUDED_METHODS = {
        # System/initialization
        "driver",
        "wait",
        "timeout",
        "__init__",
        "__new__",
        "__del__",
        # Element operations (too low-level, used internally)
        "find_element",
        "find_elements",
        "find_element_with_fallbacks",
        # Generic actions (no business context)
        "tap",
        "input_text",
        "get_text",
        "is_displayed",
        "is_enabled",
        # Fallback methods (internal retry logic)
        "tap_with_fallbacks",
        "input_text_with_fallbacks",
        "get_element_text_with_fallbacks",
        # Waiting (technical implementation details)
        "wait_for_element",
        "wait_for_text",
        "wait_for_element_to_disappear",
        # Gestures (low-level actions)
        "swipe",
        "swipe_by_percentage",
        "scroll_to_element",
        "long_press",
        # Simple navigation
        "back",
        "refresh",
        # Utility methods
        "hide_keyboard",
        "capture_screenshot",
        "get_page_source",
        "get_window_size",
        "get_device_info",
        "get_element_attribute",
        # Permission handling (internal)
        "handle_permission_popup",
    }

    def _should_exclude(method_name: str) -> bool:
        """
        Check if method should be excluded from Allure steps.

        Uses pattern-based detection for future-proof filtering.
        """
        # 1. Explicit blacklist (known infrastructure methods)
        if method_name in EXCLUDED_METHODS:
            return True

        # 2. Pattern-based exclusions (future-proof)
        # Methods ending with these patterns are typically internal helpers
        internal_patterns = [
            "_with_strategies",  # e.g., enter_otp_with_strategies
            "_with_fallbacks",  # Already in blacklist, but pattern covers future ones
            "_visible",  # e.g., is_home_screen_visible (check methods)
            "_available",  # e.g., is_element_available
            "_screen_visible",  # e.g., is_login_screen_visible
        ]

        for pattern in internal_patterns:
            if method_name.endswith(pattern):
                return True

        # 3. Convention: Methods starting with '_' are private (already handled by startswith check)

        return False

    for attr_name in dir(cls):
        # Skip private/magic methods
        if attr_name.startswith("_"):
            continue

        # Skip excluded methods (explicit + pattern-based)
        if _should_exclude(attr_name):
            continue

        attr = getattr(cls, attr_name)

        # Only wrap callable methods (not properties or class attributes)
        if callable(attr) and not isinstance(attr, (type, property, staticmethod, classmethod)):
            wrapped = _wrap_method_with_step(attr, attr_name, cls.__name__)
            setattr(cls, attr_name, wrapped)

    return cls


def _wrap_method_with_step(method: Callable, method_name: str, _class_name: str) -> Callable:
    """
    Wrap a single method with allure.step().

    Args:
        method: The method to wrap
        method_name: Name of the method
        class_name: Name of the containing class

    Returns:
        Wrapped method with Allure step reporting
    """

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        # Create human-readable step name
        step_name = _humanize_method_name(method_name, args, kwargs)

        with allure.step(step_name):
            return method(*args, **kwargs)

    # Preserve original method metadata for IDE navigation
    # functools.wraps already preserves most metadata, but we ensure these are set
    # This helps IDEs find the original method definition
    if not hasattr(wrapper, "__wrapped__"):
        wrapper.__wrapped__ = method  # Store reference to original method

    # Preserve annotations for type checking
    if hasattr(method, "__annotations__"):
        wrapper.__annotations__ = method.__annotations__

    return wrapper


def _humanize_method_name(method_name: str, args: tuple, _kwargs: dict) -> str:
    """
    Convert method name and arguments to human-readable Allure step.

    This function transforms Python method names (snake_case) into readable
    step descriptions and optionally includes the first argument value.

    Rules:
    - snake_case → Title Case with spaces
    - First argument after 'self' is included if it's a simple type
    - Sensitive data (OTP) is masked with asterisks
    - Phone numbers and other data are shown as-is

    Args:
        method_name: The Python method name (e.g., "enter_phone_number")
        args: Method arguments tuple (args[0] is always 'self')
        kwargs: Method keyword arguments

    Returns:
        Human-readable step description

    Examples:
        enter_phone_number("5550000000") → "Enter Phone Number: 5550000000"
        tap_get_otp() → "Tap Get OTP"
        enter_otp("0000") → "Enter OTP: ****"
        verify_login_success() → "Verify Login Success"
        is_displayed("element") → "Check If Element Is Displayed"
    """
    # Convert snake_case to Title Case
    readable = method_name.replace("_", " ").title()

    # Handle common verb patterns for better readability
    readable = _improve_verb_patterns(readable)

    # Add first argument if it exists and is a simple type
    if len(args) > 1:  # args[0] is always 'self'
        first_arg = args[1]

        # Only include simple types (str, int, bool, float)
        if isinstance(first_arg, (str, int, bool, float)) and first_arg not in (None, "", 0):
            # Mask sensitive data
            if any(keyword in method_name.lower() for keyword in ["otp", "password", "pin"]):
                # Mask OTP/password/PIN
                readable += f": {'*' * len(str(first_arg))}"
            elif "phone" in method_name.lower() or "mobile" in method_name.lower():
                # Show phone numbers (not sensitive in test context)
                readable += f": {first_arg}"
            elif isinstance(first_arg, bool):
                # For boolean: "allow=True" → "Allow: Yes"
                readable += f": {'Yes' if first_arg else 'No'}"
            elif isinstance(first_arg, str) and len(str(first_arg)) < 50:
                # Show short strings
                readable += f": {first_arg}"
            # Skip long strings and numbers that are likely internal IDs

    return readable


def _improve_verb_patterns(readable_name: str) -> str:
    """
    Improve readability by adjusting common verb patterns.

    Transforms:
    - "Is Something" → "Check If Something"
    - "Get Something" → "Get Something" (no change)
    - "Tap Something" → "Tap Something Button"
    - "Enter Something" → "Enter Something"
    - "Wait For Something" → "Wait For Something"
    - "Verify Something" → "Verify Something"

    Args:
        readable_name: Title-cased method name

    Returns:
        Improved readable name
    """
    # "Is" methods → "Check if"
    if readable_name.startswith("Is "):
        readable_name = "Check If" + readable_name[2:]

    # "Tap" methods → add "Button" if not already present
    if readable_name.startswith("Tap ") and "Button" not in readable_name:
        # Don't add Button if it's tapping an element type
        if not any(word in readable_name for word in ["Screen", "Element", "Field", "Input"]):
            readable_name += ""  # Keep as-is, context is clear

    # "Handle" → "Handle" (keep as-is)
    # "Wait For" → "Wait For" (keep as-is)
    # "Verify" → "Verify" (keep as-is)
    # "Get" → "Get" (keep as-is)

    return readable_name


def allure_step(step_name: str = None):
    """
    Method decorator for custom Allure step names.

    Use this decorator when you want to override the automatic step name
    generation with a custom name.

    Args:
        step_name: Custom step name. If None, uses method name.

    Returns:
        Decorator function

    Usage:
        @allure_step("Login user with credentials")
        def login(self, username, password):
            pass

        @allure_step()  # Uses method name
        def complex_action(self):
            pass
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Use custom name or humanize method name
            name = step_name if step_name else _humanize_method_name(func.__name__, args, kwargs)

            with allure.step(name):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def allure_step_with_attachment(step_name: str = None, attach_result: bool = False):
    """
    Method decorator that adds Allure step with optional result attachment.

    Useful for verification methods where you want to attach the return value
    to the Allure report.

    Args:
        step_name: Custom step name
        attach_result: If True, attaches method return value to report

    Returns:
        Decorator function

    Usage:
        @allure_step_with_attachment("Get user balance", attach_result=True)
        def get_wallet_balance(self):
            return "₹500.00"  # Attached to report
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            name = step_name if step_name else _humanize_method_name(func.__name__, args, kwargs)

            with allure.step(name):
                result = func(*args, **kwargs)

                if attach_result and result is not None:
                    # Attach result to Allure report
                    allure.attach(
                        str(result),
                        name=f"{name} - Result",
                        attachment_type=allure.attachment_type.TEXT,
                    )

                return result

        return wrapper

    return decorator
