"""Application database utility.

Direct access to the application's user profile table for test setup
(user lookup by phone, fixture injection).
NOT for test result storage — that's postgres_storage.py.
"""

import logging
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class Database:
    """Thin wrapper around psycopg2 for application profile operations."""

    def __init__(self, uri: str):
        self._uri = uri
        self._conn = None

    def _connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._uri)
            self._conn.autocommit = False
            logger.info("Connected to the app database")
        return self._conn

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.info("the app database connection closed")

    @contextmanager
    def _cursor(self, dict_cursor: bool = True):
        conn = self._connect()
        factory = RealDictCursor if dict_cursor else None
        cur = conn.cursor(cursor_factory=factory)
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    def get_user_by_phone(self, phone: str) -> dict | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, phone, campaign_tag FROM account_profiles WHERE phone = %s",
                (phone,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def set_user_campaign(self, phone: str, campaign: str) -> int | None:
        with self._cursor(dict_cursor=False) as cur:
            cur.execute(
                "UPDATE account_profiles SET campaign_tag = %s WHERE phone = %s RETURNING id",
                (campaign, phone),
            )
            row = cur.fetchone()
        user_id = row[0] if row else None
        logger.info("Set campaign_tag='%s' for phone=%s (user_id=%s)", campaign, phone, user_id)
        return user_id

    def clear_user_campaign(self, phone: str) -> None:
        with self._cursor(dict_cursor=False) as cur:
            cur.execute(
                "UPDATE account_profiles SET campaign_tag = NULL WHERE phone = %s",
                (phone,),
            )
        logger.info("Cleared campaign_tag for phone=%s", phone)

    def get_user_campaign(self, phone: str) -> str | None:
        user = self.get_user_by_phone(phone)
        return user["campaign_tag"] if user else None

    def delete_user(self, phone: str) -> None:
        """Delete user completely from DB. Backend will create fresh on next login."""
        with self._cursor(dict_cursor=False) as cur:
            cur.execute("DELETE FROM account_profiles WHERE phone = %s", (phone,))
        logger.info("User deleted: phone=%s", phone)

    def reset_user(self, phone: str, user_id: int) -> int:
        """Delete and re-insert user with same ID. Profile fields will be NULL.

        This allows us to:
        - Keep the same user_id (Redis keys stay valid)
        - Force isNewUser=false (user exists in DB when app logs in)
        - Force onboarding (profile fields are NULL)
        """
        with self._cursor(dict_cursor=False) as cur:
            cur.execute("DELETE FROM account_profiles WHERE phone = %s", (phone,))
            cur.execute(
                "INSERT INTO account_profiles (id, phone) VALUES (%s, %s)",
                (user_id, phone),
            )
        logger.info("User reset: phone=%s id=%s (profile cleared)", phone, user_id)
        return user_id
