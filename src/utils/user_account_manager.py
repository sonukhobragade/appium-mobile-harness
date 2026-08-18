"""
User Account Management Utility for the app Test Automation.

Provides utilities for managing user accounts via API calls, including:
- Triggering OTP for login
- Verifying OTP and retrieving user ID
- Deleting user accounts for test cleanup

This allows test automation to reuse the same phone numbers for "new user"
test cases by cleaning up accounts before each test run.
"""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

# Fixed OTP for demo/test users. Environment specific and deliberately not
# defaulted to a literal: a working bypass code in a public repo is a live
# weakness in whatever else shares that auth path. Set TEST_OTP.
DEFAULT_TEST_OTP = os.getenv("TEST_OTP", "")



class UserAccountManager:
    """
    Utility class for managing user accounts via the app API.

    Provides methods to trigger OTP, verify OTP, retrieve user IDs,
    and delete user accounts for test cleanup.
    """

    # API endpoints.
    #
    # API_BASE_URL has no default on purpose. These helpers create users and
    # then delete them, so a wrong or forgotten base URL is destructive; a
    # baked-in default is exactly how a suite ends up pointed somewhere nobody
    # intended. Set it explicitly, per environment.
    #
    # Read at import time because the endpoint constants below are derived from
    # it at class-definition time. See is_qa_environment() and
    # assert_safe_test_phone() for the additional guards.
    BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")

    # The base URL that counts as the throwaway environment. Also unset by
    # default, so is_qa_environment() answers False until someone says which
    # environment is safe to destroy data in. Failing closed is the point.
    QA_BASE_URL = os.getenv("QA_BASE_URL", "").rstrip("/")
    TRIGGER_OTP_ENDPOINT = f"{BASE_URL}/otp/trigger"
    VERIFY_OTP_ENDPOINT = f"{BASE_URL}/otp/verify"
    REFRESH_TOKEN_ENDPOINT = f"{BASE_URL}/auth/token"
    DELETE_USER_ENDPOINT = f"{BASE_URL}/user/deletion"
    BALANCE_ENDPOINT = f"{BASE_URL}/accounts/balance"
    USER_INFO_ENDPOINT = f"{BASE_URL}/user"
    UPDATE_USER_ENDPOINT = f"{BASE_URL}/user/update"
    CITY_SUGGEST_ENDPOINT = f"{BASE_URL}/user/cities/suggest"

    # Default headers
    DEFAULT_HEADERS = {
        "countryCode": "IN",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "appType": "android",
    }

    # Auth token for the deletion API. No default: the value is environment
    # specific and a committed fallback is how a working token outlives everyone
    # who remembers it is there. Set DELETE_AUTH_TOKEN, or leave it unset and let
    # the caller obtain a real JWT through the normal login path.
    DELETE_AUTH_TOKEN = os.getenv("DELETE_AUTH_TOKEN", "")

    @classmethod
    def is_qa_environment(cls) -> bool:
        """True when the suite is pointed at the environment declared safe.

        Both values must be set and equal. With either unset this returns
        False, which forces the explicit-phone-number path in safe_test_phone()
        rather than assuming the target is disposable.
        """
        return bool(cls.QA_BASE_URL) and cls.BASE_URL == cls.QA_BASE_URL

    @classmethod
    def safe_test_phone(cls) -> str:
        """The phone number this environment is allowed to create and delete.

        On QA every 5XXXXXXXXX number is a throwaway, so callers generate one at
        random. Off QA that band is live Indian mobile numbers, and these tests
        DELETE the account they log in as — so a random number would delete a
        real person. Off QA we therefore require an explicit opt-in number,
        which must also be registered as a demo user in that environment's
        backend config so its OTP is fixed rather than delivered by SMS.

        Raises:
            RuntimeError: off QA with no TEST_PHONE_NUMBER set.
        """
        pinned = os.getenv("TEST_PHONE_NUMBER", "").strip()
        if cls.is_qa_environment():
            return pinned  # empty string => caller generates a random QA number

        if not pinned:
            raise RuntimeError(
                f"BUG: API_BASE_URL={cls.BASE_URL} is not QA, but TEST_PHONE_NUMBER is unset.\n"
                f"These tests create a user and then DELETE it. Refusing to run against a "
                f"random phone number outside QA.\n"
                f"Set TEST_PHONE_NUMBER=<number> and register it as a demo user in "
                f"that environment's backend config so its OTP is fixed."
            )
        return pinned

    @staticmethod
    def trigger_otp(
        phone_number: str,
        country_code: str = "+91",
        country: str = "IN",
        auto_read_hash: str = os.getenv("OTP_AUTO_READ_HASH", ""),
        resend: bool = False,
    ) -> dict:
        """
        Trigger OTP for a phone number.

        Args:
            phone_number: 10-digit phone number (e.g., "5865555557")
            country_code: Country calling code (default: "+91" for India)
            country: Country code (default: "IN" for India)
            auto_read_hash: SMS auto-read hash. Defaults to OTP_AUTO_READ_HASH.
            resend: Whether this is a resend request (default: False)

        Returns:
            Dict: API response containing OTP trigger status

        Raises:
            requests.exceptions.RequestException: If API call fails

        Examples:
            >>> manager = UserAccountManager()
            >>> response = manager.trigger_otp("5865555557")
            >>> print(response)
            {'success': True, 'message': 'OTP sent'}
        """
        payload = {
            "encryptedData": phone_number,
            "phoneNumber": phone_number,
            "country": country,
            "countryCode": country_code,
            "resend": resend,
            "autoReadHash": auto_read_hash,
        }

        try:
            logger.info(f"Triggering OTP for phone number: {phone_number}")
            response = requests.post(
                UserAccountManager.TRIGGER_OTP_ENDPOINT,
                headers=UserAccountManager.DEFAULT_HEADERS,
                json=payload,
                timeout=10,
            )

            # Handle 503 with "OTP already sent" — OTP is already active, no need to resend.
            # DO NOT retry with resend=True as it corrupts the OTP on the backend.
            if response.status_code == 503:
                try:
                    error_data = response.json()
                    if "already sent" in error_data.get("message", "").lower():
                        logger.warning(f"OTP already sent for {phone_number}, proceeding to verify")
                        return {"status": "already_sent"}
                except Exception as parse_err:
                    logger.warning("Failed to parse 503 response: %s", parse_err)

            response.raise_for_status()

            result = response.json()
            logger.info(f"OTP triggered successfully for {phone_number}")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to trigger OTP for {phone_number}: {e}")
            raise

    @staticmethod
    def verify_otp(
        phone_number: str,
        otp: str,
        country_code: str = "+91",
        country: str = "IN",
        auto_read_hash: str = os.getenv("OTP_AUTO_READ_HASH", ""),
        token_type: str = "FCM",
        fcm_token: str = os.getenv("TEST_FCM_TOKEN", ""),
        appsflyer_data: str | None = None,
        campaign: str | None = None,
    ) -> dict:
        """
        Verify OTP and retrieve user data including user ID.

        Args:
            phone_number: 10-digit phone number (e.g., "5865555557")
            otp: OTP code. Environment specific; see TEST_OTP.
            country_code: Country calling code (default: "+91" for India)
            country: Country code (default: "IN" for India)
            auto_read_hash: SMS auto-read hash. Defaults to OTP_AUTO_READ_HASH.
            token_type: Push token type (default: "FCM")
            fcm_token: FCM push token (default: test token)
            appsflyer_data: AppsFlyer data JSON string (optional)

        Returns:
            Dict: API response containing:
                - user: Dict with user data including 'id' field
                - jwtToken: JWT token string for authenticated API calls
                - jwtTokenExpiry: Token expiry timestamp
                - Example: {
                    "user": {"id": 1001, ...},
                    "jwtToken": "<jwt-token>",
                    "jwtTokenExpiry": "<epoch-millis>"
                  }

        Raises:
            requests.exceptions.RequestException: If API call fails

        Examples:
            >>> manager = UserAccountManager()
            >>> response = manager.verify_otp("5550000001", os.environ["TEST_OTP"])
            >>> user_id = response['user']['id']
            >>> jwt_token = response['jwtToken']
            >>> print(f"User ID: {user_id}")
            User ID: 2047
        """
        if appsflyer_data is None:
            appsflyer_data = (
                '{"status":"success","type":"onInstallConversionDataLoaded",'
                '"data":{"af_status":"Organic","af_message":"organic install",'
                '"is_first_launch":false}}'
            )

        payload = {
            "encryptedData": phone_number,
            "phoneNumber": phone_number,
            "country": country,
            "countryCode": country_code,
            "resend": False,
            "autoReadHash": auto_read_hash,
            "otp": otp,
            "appsflyerData": appsflyer_data,
            "tokenType": token_type,
            "token": fcm_token,
        }

        if campaign:
            payload["campaign"] = campaign

        try:
            logger.info(f"Verifying OTP for phone number: {phone_number}")
            response = requests.post(
                UserAccountManager.VERIFY_OTP_ENDPOINT,
                headers=UserAccountManager.DEFAULT_HEADERS,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

            result = response.json()

            # Extract user ID and JWT token from response
            user_id = result.get("user", {}).get("id")
            jwt_token = result.get("jwtToken")  # API returns jwtToken, not token

            if user_id:
                logger.info(f"OTP verified successfully. User ID: {user_id}")
                if jwt_token:
                    logger.info(f"JWT token obtained for user {user_id}")
            else:
                logger.warning("OTP verified but no user ID found in response")

            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to verify OTP for {phone_number}: {e}")
            raise

    @staticmethod
    def delete_user(user_id: int) -> dict:
        """
        Delete a user account by user ID.

        Args:
            user_id: User ID from verify OTP response (e.g., 2047)

        Returns:
            Dict: API response containing deletion status

        Raises:
            requests.exceptions.RequestException: If API call fails

        Examples:
            >>> manager = UserAccountManager()
            >>> response = manager.delete_user(2047)
            >>> print(response)
            {'success': True, 'message': 'User deleted'}
        """
        headers = {
            **UserAccountManager.DEFAULT_HEADERS,
            "auth_token": UserAccountManager.DELETE_AUTH_TOKEN,
        }

        # Backend contract requires BOTH requestedBy + userId.
        # Missing requestedBy → 503 Service Unavailable.
        payload = {"requestedBy": "USER", "userId": user_id}

        try:
            logger.info(f"Deleting user account: {user_id}")
            response = requests.post(
                UserAccountManager.DELETE_USER_ENDPOINT, headers=headers, json=payload, timeout=10
            )
            response.raise_for_status()

            result = response.json()
            logger.info(f"User {user_id} deleted successfully")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to delete user {user_id}: {e}")
            raise

    @staticmethod
    def delete_user_by_phone(
        phone_number: str,
        otp: str = DEFAULT_TEST_OTP,
        country_code: str = "+91",
        return_token: bool = False,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> tuple[bool, int | None, str | None]:
        """
        Delete a user account by phone number (convenience method).

        This method combines trigger OTP, verify OTP, and delete user into
        a single operation. Perfect for test cleanup before running new user tests.

        Retries the full flow up to max_retries times on failure, with
        exponential backoff between attempts.

        Args:
            phone_number: 10-digit phone number (e.g., "5865555557")
            otp: OTP code. Defaults to TEST_OTP from the environment.
            country_code: Country calling code (default: "+91" for India)
            return_token: If True, returns JWT token before deletion (default: False)
            max_retries: Maximum number of retry attempts (default: 3)
            retry_delay: Base delay between retries in seconds (default: 2.0)

        Returns:
            Tuple[bool, Optional[int], Optional[str]]: (success_status, user_id_deleted, jwt_token)
                - success_status: True if deletion successful, False otherwise
                - user_id_deleted: The user ID that was deleted, or None if failed
                - jwt_token: JWT token (only if return_token=True), or None

        Examples:
            >>> manager = UserAccountManager()
            >>> success, user_id, token = manager.delete_user_by_phone("5865555557")
            >>> if success:
            ...     print(f"Successfully deleted user {user_id}")
            Successfully deleted user 2047

            >>> # Use in test setup
            >>> def setup_method(self):
            ...     # Clean up account before new user test
            ...     UserAccountManager.delete_user_by_phone("5550000000")
        """
        import time

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"Starting user deletion flow for {phone_number} "
                    f"(attempt {attempt}/{max_retries})"
                )

                # Step 1: Trigger OTP
                UserAccountManager.trigger_otp(phone_number, country_code)

                # Step 2: Verify OTP and get user ID + token
                verify_response = UserAccountManager.verify_otp(phone_number, otp, country_code)
                user_id = verify_response.get("user", {}).get("id")
                jwt_token = verify_response.get("jwtToken") if return_token else None

                if not user_id:
                    raise ValueError(f"No user ID found for {phone_number}")

                # Step 3: Delete user
                UserAccountManager.delete_user(user_id)

                logger.info(
                    f"Successfully deleted user {user_id} "
                    f"(phone: {phone_number}) on attempt {attempt}"
                )
                return True, user_id, jwt_token

            except Exception as e:
                _last_error = e
                if attempt < max_retries:
                    delay = retry_delay * attempt  # Linear backoff: 2s, 4s, 6s
                    logger.warning(
                        f"Attempt {attempt}/{max_retries} failed for "
                        f"{phone_number}: {e}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"All {max_retries} attempts failed for {phone_number}. Last error: {e}"
                    )

        return False, None, None

    @staticmethod
    def get_user_id(phone_number: str, otp: str = DEFAULT_TEST_OTP) -> int | None:
        """
        Get user ID for a phone number without deleting the account.

        Useful for retrieving user IDs for verification purposes.

        Args:
            phone_number: 10-digit phone number (e.g., "5865555557")
            otp: OTP code. Defaults to TEST_OTP from the environment.

        Returns:
            Optional[int]: User ID if found, None otherwise

        Examples:
            >>> manager = UserAccountManager()
            >>> user_id = manager.get_user_id("5550000000")
            >>> print(f"User ID: {user_id}")
            User ID: 2047
        """
        try:
            # Trigger OTP
            UserAccountManager.trigger_otp(phone_number)

            # Verify OTP and extract user ID
            verify_response = UserAccountManager.verify_otp(phone_number, otp)
            user_id = verify_response.get("user", {}).get("id")

            if user_id:
                logger.info(f"Retrieved user ID {user_id} for phone {phone_number}")
                return user_id
            else:
                logger.warning(f"No user ID found for {phone_number}")
                return None

        except Exception as e:
            logger.error(f"Failed to get user ID for {phone_number}: {e}")
            return None

    @staticmethod
    def login_and_get_token(
        phone_number: str, otp: str = DEFAULT_TEST_OTP, country_code: str = "+91"
    ) -> tuple[int | None, str | None]:
        """
        Login user and retrieve JWT token for authenticated API calls.

        This is a convenience method for tests that need to make authenticated
        API calls (e.g., balance checks, transaction history, chat operations).

        Args:
            phone_number: 10-digit phone number (e.g., "5550000000")
            otp: OTP code. Defaults to TEST_OTP from the environment.
            country_code: Country calling code (default: "+91" for India)

        Returns:
            Tuple[Optional[int], Optional[str]]: (user_id, jwt_token)
                - user_id: User ID from login
                - jwt_token: JWT token for authenticated API calls

        Examples:
            >>> manager = UserAccountManager()
            >>> user_id, token = manager.login_and_get_token("5550000000")
            >>> if token:
            ...     balance = manager.get_balance(token)
            ...     print(f"User {user_id}: ₹{balance['depositBalance']}, {balance['providerCoinsBalance']} coins")
            User 2047: ₹100, 50 coins

            >>> # Use in test for balance validation
            >>> def test_add_money(self):
            ...     user_id, token = UserAccountManager.login_and_get_token("5550000000")
            ...     before = UserAccountManager.get_balance(token)
            ...     # ... perform add money operation
            ...     after = UserAccountManager.get_balance(token)
            ...     assert after['depositBalance'] > before['depositBalance']
        """
        try:
            logger.info(f"Logging in and retrieving token for {phone_number}")

            # Step 1: Trigger OTP
            UserAccountManager.trigger_otp(phone_number, country_code)

            # Step 2: Verify OTP and get user ID + token
            verify_response = UserAccountManager.verify_otp(phone_number, otp, country_code)
            user_id = verify_response.get("user", {}).get("id")
            jwt_token = verify_response.get("jwtToken")

            if user_id and jwt_token:
                logger.info(f"Login successful. User ID: {user_id}, Token obtained")
                return user_id, jwt_token
            else:
                logger.error("Login failed. Missing user ID or token in response")
                return None, None

        except Exception as e:
            logger.error(f"Failed to login and get token for {phone_number}: {e}")
            return None, None

    @staticmethod
    def refresh_token(user_id: int, phone_number: str) -> str | None:
        """Refresh expired JWT token via POST /auth/token.

        Token expires every 15 mins. Call this when any API returns 401.

        Args:
            user_id: User ID from login
            phone_number: User's phone number

        Returns:
            New JWT token string, or None if refresh fails.
        """
        logger.info(f"Refreshing JWT token for user_id={user_id}, phone={phone_number}")
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    UserAccountManager.REFRESH_TOKEN_ENDPOINT,
                    headers={
                        **UserAccountManager.DEFAULT_HEADERS,
                        "userId": str(user_id),
                        "phoneNumber": phone_number,
                    },
                    timeout=15,
                )
                response.raise_for_status()
                new_token = response.json().get("jwtToken", "")
                if new_token:
                    logger.info(f"Token refreshed successfully for user_id={user_id}")
                    return new_token
                logger.error("Token refresh response missing jwtToken")
                return None
            except requests.exceptions.RequestException as e:
                if attempt < 3:
                    logger.warning(
                        f"Token refresh attempt {attempt}/3 failed: {e}. Retrying in {attempt * 2}s..."
                    )
                    time.sleep(attempt * 2)
                else:
                    logger.error(
                        f"Failed to refresh token after 3 attempts for user_id={user_id}: {e}"
                    )
                    return None

    @staticmethod
    def get_balance(jwt_token: str, relationship_id: int = 0) -> dict | None:
        """
        Get user account balance (deposit balance and Provider Coins).

        This method requires a valid JWT token obtained from login/verify_otp.

        Args:
            jwt_token: JWT token from verify_otp response (e.g., "<jwt-token>")
            relationship_id: Relationship ID (default: 0)

        Returns:
            Dict: Balance information containing:
                - depositBalance: Numeric value of deposit balance (e.g., 100)
                - providerCoinsBalance: Numeric value of Provider Coins (e.g., 50)
                - Example: {"depositBalance": 100, "providerCoinsBalance": 50}
            None: If API call fails

        Raises:
            requests.exceptions.RequestException: If API call fails

        Examples:
            >>> manager = UserAccountManager()
            >>> # First login to get token
            >>> user_id, token = manager.login_and_get_token("5550000000")
            >>> # Then get balance
            >>> balance = manager.get_balance(token)
            >>> print(f"Deposit Balance: ₹{balance['depositBalance']}")
            >>> print(f"Provider Coins: {balance['providerCoinsBalance']}")
            Deposit Balance: ₹100
            Provider Coins: 50

            >>> # Use in test for balance validation
            >>> def test_add_money_success(self):
            ...     user_id, token = UserAccountManager.login_and_get_token("5550000000")
            ...     before = UserAccountManager.get_balance(token)
            ...     # ... perform add money operation via UI
            ...     after = UserAccountManager.get_balance(token)
            ...     assert after['depositBalance'] == before['depositBalance'] + 500
        """
        headers = {
            **UserAccountManager.DEFAULT_HEADERS,
            "auth_token": jwt_token,
            "relationshipId": str(relationship_id),
        }

        try:
            logger.info("Fetching user balance")
            response = requests.get(
                UserAccountManager.BALANCE_ENDPOINT, headers=headers, timeout=10
            )
            response.raise_for_status()

            result = response.json()

            deposit_balance = result.get("depositBalance")
            provider_coins_balance = result.get("providerCoinsBalance")

            logger.info(
                f"Balance retrieved successfully: "
                f"Deposit=₹{deposit_balance}, Provider Coins={provider_coins_balance}"
            )

            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get balance: {e}")
            raise

    @staticmethod
    def get_user_info(jwt_token: str) -> dict:
        """
        Get user profile information (name, DOB, TOB, POB, gender, consultation data).

        Unlike api_fetcher (which uses a cached QA JWT for session-level data),
        this method accepts an explicit JWT to fetch profile data for a specific
        user (e.g., a just-created user during a test).

        Args:
            jwt_token: JWT token from verify_otp response.

        Returns:
            Dict with user profile fields (id, phone, name, dob, tob, pob, gender,
            country, segment, primaryCategory, baseline, etc.).

        Raises:
            requests.exceptions.RequestException: If API call fails.
        """
        headers = {
            **UserAccountManager.DEFAULT_HEADERS,
            "auth_token": jwt_token,
        }

        try:
            logger.info("Fetching user info from /user API")
            response = requests.get(
                UserAccountManager.USER_INFO_ENDPOINT, headers=headers, timeout=10
            )
            response.raise_for_status()
            result = response.json()

            logger.info(
                f"User info retrieved: name='{result.get('name')}', "
                f"dob='{result.get('dob')}', pob='{result.get('pob')}'"
            )
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get user info: {e}")
            raise

    @staticmethod
    def suggest_city(jwt_token: str, city_name: str) -> dict:
        """Fetch city suggestion from /user/cities/suggest API.

        Returns the first matching city with id, lat, lon, state.
        The backend requires city data from this API for proper onboarding.

        Args:
            jwt_token: JWT token for auth.
            city_name: City name to search (e.g., "Bengaluru").

        Returns:
            Dict with id, city, state, lat, lon.

        Raises:
            requests.exceptions.RequestException: If API call fails.
            ValueError: If no matching city found.
        """
        headers = {
            **UserAccountManager.DEFAULT_HEADERS,
            "auth_token": jwt_token,
        }
        response = requests.get(
            UserAccountManager.CITY_SUGGEST_ENDPOINT,
            headers=headers,
            params={"query": city_name, "limit": 5},
            timeout=10,
        )
        response.raise_for_status()
        cities = response.json()
        if not cities:
            raise ValueError(f"No city found for query '{city_name}'")
        # Pick exact match if available, otherwise first result
        for city in cities:
            if city.get("city", "").lower() == city_name.lower():
                logger.info(
                    f"City resolved: {city['city']}, {city.get('state', '')} (id={city['id']})"
                )
                return city
        logger.info(f"City resolved (first match): {cities[0]['city']} (id={cities[0]['id']})")
        return cities[0]

    @staticmethod
    def complete_onboarding(jwt_token: str, profile: dict) -> dict:
        """Complete onboarding via city suggest + POST /user/update.

        Fetches real city data from /user/cities/suggest (backend requires it),
        then sets user profile so the app treats the user as fully onboarded.

        Args:
            jwt_token: JWT token from login_and_get_token().
            profile: Dict with name, dob, tob, pob, latitude, longitude, gender.

        Returns:
            Dict: Updated user profile from API response.

        Raises:
            requests.exceptions.RequestException: If API call fails.
        """
        # Fetch real city data from suggest API (backend requires this)
        city_data = UserAccountManager.suggest_city(jwt_token, profile["pob"])

        body = {
            "name": profile["name"],
            "dob": profile["dob"],
            "tob": profile["tob"],
            "pob": f"{city_data['city']}, {city_data.get('state', '')}",
            "issues": ["career"],
            "details": "{}",
            "languages": ["English"],
            "latitude": str(city_data["lat"]),
            "longitude": str(city_data["lon"]),
            "gender": profile["gender"],
        }

        headers = {
            **UserAccountManager.DEFAULT_HEADERS,
            "auth_token": jwt_token,
            "isProfileChangeNeeded": "true",
        }

        try:
            logger.info(
                f"Completing onboarding via API: {profile['name']}, "
                f"{profile['gender']}, {profile['dob']}, {city_data['city']}"
            )
            response = requests.post(
                UserAccountManager.UPDATE_USER_ENDPOINT,
                headers=headers,
                json=body,
                timeout=15,
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"Onboarding complete via API for '{profile['name']}'")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to complete onboarding via API: {e}")
            raise
