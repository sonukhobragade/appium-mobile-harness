"""
Random data generators for test automation.

Provides utilities for generating random test data like phone numbers,
emails, names, etc. for use in automated tests.
"""

import random
from typing import Literal


class RandomDataGenerator:
    """
    Utility class for generating random test data.

    This class provides static methods for generating various types of
    random data commonly needed in test automation, including phone numbers,
    emails, and other test data.
    """

    # Valid Indian mobile number prefixes (first digit)
    INDIAN_MOBILE_PREFIXES = ["0", "1", "2"]

    # Placeholder names, deliberately.
    #
    # This is a generator of synthetic test users, so it needs name-shaped
    # strings. It previously used pools of common real first and last names,
    # which identify nobody but are indistinguishable from real user data at a
    # glance — the wrong thing to ship in a repository whose whole point is
    # that it contains no real data. These are the placeholder names used in
    # testing and cryptography literature: nobody mistakes Alice and Bob for a
    # customer record.
    # Male first names
    MALE_FIRST_NAMES = [
        "Alan", "Bob", "Carl", "Dave", "Erik",
        "Frank", "Gus", "Hugo", "Ivan", "Jack",
        "Karl", "Leon", "Marco", "Nils", "Omar",
        "Paul", "Quinn", "Rolf", "Sven", "Theo",
        "Umar", "Victor", "Walter", "Xavi", "Yuri",
        "Zane",
    ]

    # Female first names
    FEMALE_FIRST_NAMES = [
        "Alice", "Beth", "Carol", "Dana", "Erin",
        "Fiona", "Grace", "Heidi", "Iris", "Judy",
        "Kira", "Lena", "Mara", "Nina", "Olive",
        "Peggy", "Quinn", "Rosa", "Sybil", "Tina",
        "Uma", "Vera", "Wanda", "Xena", "Yara",
        "Zoe",
    ]

    # Last names
    LAST_NAMES = [
        "Archer", "Baker", "Carter", "Draper", "Ellis",
        "Fisher", "Gardner", "Harper", "Ingram", "Jenkins",
        "Keller", "Lawson", "Mercer", "Norton", "Osborne",
        "Palmer", "Quincy", "Reeves", "Sawyer", "Turner",
        "Underwood", "Vaughn", "Whitaker", "Xander", "Young",
        "Zimmer",
    ]

    @staticmethod
    def generate_mobile_number(
        prefix: str | None = None, length: int = 10, country: Literal["IN"] = "IN"
    ) -> str:
        """
        Generate a random mobile number.

        Args:
            prefix: First digit(s) of the mobile number. If None, randomly selects
                   from valid prefixes based on country. For India: 9, 8, 7, or 6.
            length: Total length of the mobile number (default: 10 for Indian numbers)
            country: Country code (currently only supports "IN" for India)

        Returns:
            str: A random mobile number as a string (e.g., "5550000000")

        Examples:
            >>> # Generate random Indian mobile number starting with 9
            >>> RandomDataGenerator.generate_mobile_number(prefix="9")
            '5550000000'

            >>> # Generate random Indian mobile number with any valid prefix
            >>> RandomDataGenerator.generate_mobile_number()
            '5550000000'

            >>> # Generate 8-digit number (for testing edge cases)
            >>> RandomDataGenerator.generate_mobile_number(length=8)
            '91234567'

        Note:
            For Indian mobile numbers:
            - Length is typically 10 digits
            - First digit is usually 9, 8, 7, or 6
            - Numbers are returned as strings to preserve leading zeros
        """
        if country != "IN":
            raise ValueError(
                f"Country '{country}' not supported. Only 'IN' is currently supported."
            )

        # Select prefix based on country
        if prefix is None:
            prefix = random.choice(RandomDataGenerator.INDIAN_MOBILE_PREFIXES)
        elif prefix not in RandomDataGenerator.INDIAN_MOBILE_PREFIXES:
            # Allow custom prefix but warn it might not be valid
            pass

        # Validate length
        if length < len(prefix):
            raise ValueError(f"Length {length} must be >= prefix length {len(prefix)}")

        # Generate remaining digits
        remaining_digits = length - len(prefix)
        random_digits = "".join([str(random.randint(0, 9)) for _ in range(remaining_digits)])

        return f"{prefix}{random_digits}"

    @staticmethod
    def generate_email(domain: str = "test.com", name_length: int = 8) -> str:
        """
        Generate a random email address.

        Args:
            domain: Email domain (default: "test.com")
            name_length: Length of the username part (default: 8)

        Returns:
            str: A random email address (e.g., "abcd1234@test.com")

        Examples:
            >>> RandomDataGenerator.generate_email()
            'xyz12345@test.com'

            >>> RandomDataGenerator.generate_email(domain="example.org")
            'abc98765@example.org'
        """
        username = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=name_length))
        return f"{username}@{domain}"

    @staticmethod
    def generate_random_otp(length: int = 4) -> str:
        """
        Generate a random numeric string.

        Useful for generating OTPs, PINs, or other numeric codes.

        Args:
            length: Length of the numeric string (default: 4)

        Returns:
            str: A random numeric string (e.g., "1234")

        Examples:
            >>> RandomDataGenerator.generate_numeric_string(4)
            '5678'

            >>> RandomDataGenerator.generate_numeric_string(6)
            '123456'
        """
        return "".join([str(random.randint(0, 9)) for _ in range(length)])

    @staticmethod
    def generate_gender() -> Literal["male", "female"]:
        """
        Generate a random gender value.

        Returns:
            str: Either "male" or "female" randomly selected

        Examples:
            >>> RandomDataGenerator.generate_gender()
            'male'

            >>> RandomDataGenerator.generate_gender()
            'female'
        """
        return random.choice(["male", "female"])

    # Common Indian cities (for place of birth selection) — 50 cities
    INDIAN_CITIES = [
        "Agra", "Ahmedabad", "Ajmer", "Allahabad", "Amritsar",
        "Aurangabad", "Bangalore", "Bhopal", "Bhubaneswar", "Chandigarh",
        "Chennai", "Coimbatore", "Dehradun", "Delhi", "Dhanbad",
        "Faridabad", "Ghaziabad", "Goa", "Gurgaon", "Guwahati",
        "Gwalior", "Haridwar", "Hyderabad", "Indore", "Jaipur",
        "Jalandhar", "Jammu", "Jamshedpur", "Jodhpur", "Kanpur",
        "Kochi", "Kolkata", "Lucknow", "Ludhiana", "Madurai",
        "Mangalore", "Meerut", "Mumbai", "Mysore", "Nagpur",
        "Nashik", "Noida", "Patna", "Pune", "Raipur",
        "Rajkot", "Ranchi", "Surat", "Vadodara", "Varanasi",
    ]

    @staticmethod
    def generate_indian_city() -> str:
        """
        Generate a random Indian city with state name.

        Returns:
            str: City name with state (e.g., "Mumbai, Maharashtra")

        Examples:
            >>> RandomDataGenerator.generate_indian_city()
            'Bangalore, Karnataka'

            >>> RandomDataGenerator.generate_indian_city()
            'Jaipur, Rajasthan'
        """
        return random.choice(RandomDataGenerator.INDIAN_CITIES)

    @staticmethod
    def generate_first_name(gender: Literal["male", "female"] | None = None) -> str:
        """
        Generate a random first name.

        Args:
            gender: Gender for the name ("male" or "female").
                   If None, randomly selects from all names.

        Returns:
            str: A random first name (e.g., "Bob", "Alice")

        Examples:
            >>> RandomDataGenerator.generate_first_name("male")
            'Bob'

            >>> RandomDataGenerator.generate_first_name("female")
            'Alice'

            >>> RandomDataGenerator.generate_first_name()
            'Quinn'  # Could be male or female
        """
        if gender == "male":
            return random.choice(RandomDataGenerator.MALE_FIRST_NAMES)
        elif gender == "female":
            return random.choice(RandomDataGenerator.FEMALE_FIRST_NAMES)
        else:
            # Random gender if not specified
            all_names = (
                RandomDataGenerator.MALE_FIRST_NAMES
                + RandomDataGenerator.FEMALE_FIRST_NAMES
            )
            return random.choice(all_names)

    @staticmethod
    def generate_last_name() -> str:
        """
        Generate a random last name.

        Returns:
            str: A random last name (e.g., "Archer", "Baker")

        Examples:
            >>> RandomDataGenerator.generate_last_name()
            'Carter'

            >>> RandomDataGenerator.generate_last_name()
            'Draper'
        """
        return random.choice(RandomDataGenerator.LAST_NAMES)

    @staticmethod
    def generate_full_name(gender: Literal["male", "female"] | None = None) -> str:
        """
        Generate a random full name (first + last).

        Gender is randomly selected by default for maximum randomness.

        Args:
            gender: Gender for the first name ("male" or "female").
                   If None (default), randomly selects between male and female.

        Returns:
            str: A random full name (e.g., "Bob Archer", "Alice Baker")

        Examples:
            >>> RandomDataGenerator.generate_full_name()  # Random gender
            'Bob Archer'

            >>> RandomDataGenerator.generate_full_name()  # Random gender
            'Alice Draper'

            >>> RandomDataGenerator.generate_full_name("male")  # Specific gender
            'Quinn Baker'
        """
        # Randomly select gender if not specified
        if gender is None:
            gender = random.choice(["male", "female"])

        first_name = RandomDataGenerator.generate_first_name(gender)
        last_name = RandomDataGenerator.generate_last_name()
        return f"{first_name} {last_name}"
