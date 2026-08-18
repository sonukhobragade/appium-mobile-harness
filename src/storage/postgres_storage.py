"""
PostgreSQL storage module for test results
Handles all database operations for test result persistence
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from src.data.models.test_models import (
    Screenshot,
    TestResult,
    TestStatus,
    TestSuite,
)

logger = logging.getLogger(__name__)


class PostgresStorage:
    """PostgreSQL storage handler for test results"""

    def __init__(self, uri: str, schema: str = "public"):
        """
        Initialize PostgreSQL connection

        Args:
            uri: PostgreSQL connection URI
            schema: Database schema to use
        """
        self.uri = uri
        self.schema = schema
        self.connection = None
        self._connect()
        self._ensure_schema()

    def _connect(self):
        """Establish database connection"""
        try:
            self.connection = psycopg2.connect(self.uri)
            self.connection.autocommit = False
            logger.info("Connected to PostgreSQL database")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    @contextmanager
    def get_cursor(self, dict_cursor: bool = True):
        """Get database cursor with transaction management"""
        cursor_factory = RealDictCursor if dict_cursor else None
        cursor = self.connection.cursor(cursor_factory=cursor_factory)

        try:
            yield cursor
            self.connection.commit()
        except Exception as e:
            self.connection.rollback()
            logger.error(f"Database transaction failed: {e}")
            raise
        finally:
            cursor.close()

    def _ensure_schema(self):
        """Ensure required tables exist"""
        create_tables_sql = f"""
        CREATE SCHEMA IF NOT EXISTS {self.schema};

        CREATE TABLE IF NOT EXISTS {self.schema}.test_suites (
            suite_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suite_name VARCHAR(255) NOT NULL,
            description TEXT,
            platform VARCHAR(50) NOT NULL,
            device_id VARCHAR(255),
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            status VARCHAR(50) NOT NULL,
            config JSONB,
            test_count INTEGER DEFAULT 0,
            passed_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS {self.schema}.test_runs (
            run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suite_id UUID REFERENCES {self.schema}.test_suites(suite_id),
            test_name VARCHAR(255) NOT NULL,
            platform VARCHAR(50) NOT NULL,
            device_id VARCHAR(255),
            device_name VARCHAR(255),
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            status VARCHAR(50) NOT NULL,
            duration FLOAT,
            error_message TEXT,
            stack_trace TEXT,
            retry_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS {self.schema}.test_results (
            result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID REFERENCES {self.schema}.test_runs(run_id),
            step_name VARCHAR(255),
            status VARCHAR(50),
            error_message TEXT,
            metadata JSONB,
            timestamp TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS {self.schema}.artifacts (
            artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID REFERENCES {self.schema}.test_runs(run_id),
            type VARCHAR(50) NOT NULL,
            name VARCHAR(255),
            path TEXT,
            content TEXT,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS {self.schema}.test_data (
            data_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID REFERENCES {self.schema}.test_runs(run_id),
            input_data JSONB,
            output_data JSONB,
            timestamp TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS {self.schema}.device_info (
            device_id VARCHAR(255) PRIMARY KEY,
            platform VARCHAR(50) NOT NULL,
            os_version VARCHAR(50),
            device_name VARCHAR(255),
            capabilities JSONB,
            available BOOLEAN DEFAULT TRUE,
            last_used TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS {self.schema}.test_logs (
            log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID REFERENCES {self.schema}.test_runs(run_id),
            timestamp TIMESTAMP NOT NULL,
            level VARCHAR(20),
            message TEXT,
            details JSONB
        );

        -- Create indexes for performance
        CREATE INDEX IF NOT EXISTS idx_test_runs_suite_id
            ON {self.schema}.test_runs(suite_id);

        CREATE INDEX IF NOT EXISTS idx_test_runs_status
            ON {self.schema}.test_runs(status);

        CREATE INDEX IF NOT EXISTS idx_test_results_run_id
            ON {self.schema}.test_results(run_id);

        CREATE INDEX IF NOT EXISTS idx_artifacts_run_id
            ON {self.schema}.artifacts(run_id);

        CREATE INDEX IF NOT EXISTS idx_test_logs_run_id
            ON {self.schema}.test_logs(run_id);

        CREATE INDEX IF NOT EXISTS idx_test_suites_created_at
            ON {self.schema}.test_suites(created_at DESC);
        """

        try:
            with self.get_cursor(dict_cursor=False) as cursor:
                cursor.execute(create_tables_sql)
            logger.info("Database schema ensured")
        except Exception as e:
            logger.error(f"Failed to create schema: {e}")
            raise

    def create_test_suite(self, suite: TestSuite) -> str:
        """
        Create a new test suite record

        Args:
            suite: TestSuite model

        Returns:
            Suite ID
        """
        sql = f"""
        INSERT INTO {self.schema}.test_suites
        (suite_name, description, platform, device_id, start_time, status, config)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING suite_id
        """

        with self.get_cursor() as cursor:
            cursor.execute(
                sql,
                (
                    suite.suite_name,
                    suite.description,
                    suite.platform.value,
                    suite.device_id,
                    suite.start_time,
                    suite.status.value,
                    Json(suite.config),
                ),
            )
            result = cursor.fetchone()
            suite_id = str(result["suite_id"])

        logger.info(f"Created test suite: {suite_id}")
        return suite_id

    def store_test_result(self, result: TestResult) -> str:
        """
        Store test execution result

        Args:
            result: TestResult model

        Returns:
            Run ID
        """
        # Create test run record
        run_sql = f"""
        INSERT INTO {self.schema}.test_runs
        (suite_id, test_name, platform, device_id, start_time, end_time,
         status, duration, error_message, stack_trace, retry_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING run_id
        """

        with self.get_cursor() as cursor:
            cursor.execute(
                run_sql,
                (
                    result.suite_id,
                    result.test_name,
                    result.platform.value,
                    result.device_id,
                    result.start_time,
                    result.end_time or datetime.now(),
                    result.status.value,
                    result.duration,
                    result.error_message,
                    result.stack_trace,
                    result.retry_count,
                ),
            )
            run_id = str(cursor.fetchone()["run_id"])

            # Store test data
            if result.test_data:
                data_sql = f"""
                INSERT INTO {self.schema}.test_data
                (run_id, input_data, timestamp)
                VALUES (%s, %s, %s)
                """
                cursor.execute(data_sql, (run_id, Json(result.test_data), result.start_time))

            # Store logs
            if result.logs:
                for log in result.logs:
                    log_sql = f"""
                    INSERT INTO {self.schema}.test_logs
                    (run_id, timestamp, level, message, details)
                    VALUES (%s, %s, %s, %s, %s)
                    """

                    if isinstance(log, dict):
                        cursor.execute(
                            log_sql,
                            (
                                run_id,
                                log.get("timestamp", datetime.now()),
                                log.get("level", "INFO"),
                                log.get("message", str(log)),
                                Json(log.get("details", {})),
                            ),
                        )
                    else:
                        cursor.execute(log_sql, (run_id, datetime.now(), "INFO", str(log), None))

            # Store screenshots
            if result.screenshots:
                for screenshot in result.screenshots:
                    artifact_sql = f"""
                    INSERT INTO {self.schema}.artifacts
                    (run_id, type, name, path, content, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """

                    if isinstance(screenshot, Screenshot):
                        cursor.execute(
                            artifact_sql,
                            (
                                run_id,
                                "screenshot",
                                screenshot.filename,
                                screenshot.path,
                                screenshot.data,
                                Json({"timestamp": screenshot.timestamp.isoformat()}),
                            ),
                        )
                    elif isinstance(screenshot, dict):
                        cursor.execute(
                            artifact_sql,
                            (
                                run_id,
                                "screenshot",
                                screenshot.get("filename"),
                                screenshot.get("path"),
                                screenshot.get("data"),
                                Json(screenshot),
                            ),
                        )

        logger.info(f"Stored test result for: {result.test_name}")
        return run_id

    def update_suite_status(
        self, suite_id: str, status: str, test_results: list[TestResult] = None
    ):
        """Update test suite status and statistics"""
        sql = f"""
        UPDATE {self.schema}.test_suites
        SET status = %s,
            end_time = %s,
            test_count = %s,
            passed_count = %s,
            failed_count = %s
        WHERE suite_id = %s
        """

        if test_results:
            test_count = len(test_results)
            passed_count = sum(1 for r in test_results if r.status == TestStatus.PASSED)
            failed_count = sum(1 for r in test_results if r.status == TestStatus.FAILED)
        else:
            test_count = passed_count = failed_count = 0

        with self.get_cursor() as cursor:
            cursor.execute(
                sql, (status, datetime.now(), test_count, passed_count, failed_count, suite_id)
            )

    def get_suite_results(self, suite_id: str) -> dict[str, Any]:
        """Get complete suite results with all tests"""
        suite_sql = f"""
        SELECT * FROM {self.schema}.test_suites
        WHERE suite_id = %s
        """

        runs_sql = f"""
        SELECT * FROM {self.schema}.test_runs
        WHERE suite_id = %s
        ORDER BY start_time
        """

        with self.get_cursor() as cursor:
            cursor.execute(suite_sql, (suite_id,))
            suite = cursor.fetchone()

            cursor.execute(runs_sql, (suite_id,))
            runs = cursor.fetchall()

        return {"suite": suite, "test_runs": runs}

    def get_test_history(self, test_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get historical results for a specific test"""
        sql = f"""
        SELECT tr.*, ts.suite_name
        FROM {self.schema}.test_runs tr
        JOIN {self.schema}.test_suites ts ON tr.suite_id = ts.suite_id
        WHERE tr.test_name = %s
        ORDER BY tr.start_time DESC
        LIMIT %s
        """

        with self.get_cursor() as cursor:
            cursor.execute(sql, (test_name, limit))
            return cursor.fetchall()

    def get_failure_analysis(
        self, platform: str | None = None, days: int = 7
    ) -> list[dict[str, Any]]:
        """Analyze test failures over time"""
        sql = f"""
        SELECT
            test_name,
            COUNT(*) as total_runs,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failures,
            AVG(duration) as avg_duration,
            MAX(error_message) as last_error
        FROM {self.schema}.test_runs
        WHERE start_time >= NOW() - INTERVAL '{days} days'
        {" AND platform = %s" if platform else ""}
        GROUP BY test_name
        HAVING SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) > 0
        ORDER BY failures DESC
        """

        with self.get_cursor() as cursor:
            if platform:
                cursor.execute(sql, (platform,))
            else:
                cursor.execute(sql)
            return cursor.fetchall()

    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            logger.info("PostgreSQL connection closed")
