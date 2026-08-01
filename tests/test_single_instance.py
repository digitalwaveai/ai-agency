import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from leadpilot.single_instance import (
    TelegramPollerLock,
    poller_lock_key,
    run_single_telegram_poller,
)


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.executed = []
        self.closed = False

    def execute(self, statement, params=()):
        self.executed.append((statement, params))
        if "pg_try_advisory_lock" in statement:
            return FakeCursor({"acquired": self.acquired})
        return FakeCursor({})

    def close(self):
        self.closed = True


class FakeDatabase:
    def __init__(self, connection, *, is_postgres=True):
        self.connection = connection
        self.is_postgres = is_postgres

    def _connect(self):
        return self.connection


class SingleInstanceTests(unittest.TestCase):
    def test_lock_key_is_stable_and_token_specific(self):
        first = poller_lock_key("123:token-a")
        self.assertEqual(first, poller_lock_key("123:token-a"))
        self.assertNotEqual(first, poller_lock_key("123:token-b"))
        self.assertGreaterEqual(first, -(2**63))
        self.assertLess(first, 2**63)

    def test_postgres_lock_is_held_until_release(self):
        connection = FakeConnection(acquired=True)
        database = FakeDatabase(connection)
        with patch("leadpilot.single_instance.Database", return_value=database):
            lock = TelegramPollerLock("postgresql://test", "123:token")
            self.assertTrue(lock.acquire())
            self.assertFalse(connection.closed)
            lock.release()

        statements = [item[0] for item in connection.executed]
        self.assertTrue(any("pg_try_advisory_lock" in item for item in statements))
        self.assertTrue(any("pg_advisory_unlock" in item for item in statements))
        self.assertTrue(connection.closed)

    def test_second_postgres_process_does_not_start_polling(self):
        connection = FakeConnection(acquired=False)
        database = FakeDatabase(connection)
        with patch("leadpilot.single_instance.Database", return_value=database):
            lock = TelegramPollerLock("postgresql://test", "123:token")
            self.assertFalse(lock.acquire())
        self.assertTrue(connection.closed)

    def test_runner_waits_for_lock_then_runs_once(self):
        fake_lock = Mock()
        fake_lock.acquire.side_effect = [False, True]
        settings = SimpleNamespace(
            database_url="postgresql://test",
            telegram_bot_token="123:token",
        )
        run_bot = Mock()

        with (
            patch(
                "leadpilot.single_instance.TelegramPollerLock",
                return_value=fake_lock,
            ),
            patch("leadpilot.single_instance.time.sleep") as sleep,
        ):
            run_single_telegram_poller(
                run_bot,
                settings_factory=lambda: settings,
                retry_seconds=1,
            )

        self.assertEqual(fake_lock.acquire.call_count, 2)
        sleep.assert_called_once_with(1.0)
        run_bot.assert_called_once_with()
        fake_lock.release.assert_called_once_with()

    def test_sqlite_development_mode_does_not_require_postgres_lock(self):
        connection = FakeConnection()
        database = FakeDatabase(connection, is_postgres=False)
        with patch("leadpilot.single_instance.Database", return_value=database):
            lock = TelegramPollerLock("sqlite:///test.db", "123:token")
            self.assertTrue(lock.acquire())
        self.assertEqual(connection.executed, [])


if __name__ == "__main__":
    unittest.main()
