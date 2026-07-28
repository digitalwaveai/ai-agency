from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Callable

PERSISTED_FIELDS = ("leads", "radars")


def _as_datetime(database: Any, value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parser = getattr(database, "_as_datetime", None)
        parsed = parser(value) if parser else (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def _db_datetime(database: Any, value: datetime) -> datetime | str:
    formatter = getattr(database, "_db_datetime", None)
    return formatter(value) if formatter else value


def _period_start(
    database: Any,
    user_id: int,
    access: dict[str, Any],
) -> datetime | None:
    if str(access.get("source") or "") == "trial":
        statement = database._sql(
            "SELECT created_at FROM user_accounts WHERE user_id = ?"
        )
        parameters: tuple[object, ...] = (user_id,)
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        statement = database._sql(
            """
            SELECT starts_at
            FROM subscriptions
            WHERE user_id = ? AND starts_at <= ? AND ends_at > ?
            ORDER BY ends_at DESC
            LIMIT 1
            """
        )
        current = _db_datetime(database, now)
        parameters = (user_id, current, current)

    with database._connect() as connection:
        row = connection.execute(statement, parameters).fetchone()
    if not row:
        return None
    key = "created_at" if str(access.get("source") or "") == "trial" else "starts_at"
    return _as_datetime(database, row[key])


def _count_persisted(
    database: Any,
    table: str,
    user_id: int,
    start: datetime,
    end: datetime,
) -> int:
    if table not in PERSISTED_FIELDS:
        raise ValueError("Unsupported usage table")
    statement = database._sql(
        f"""
        SELECT COUNT(*) AS total
        FROM {table}
        WHERE user_id = ? AND created_at >= ? AND created_at < ?
        """
    )
    with database._connect() as connection:
        row = connection.execute(
            statement,
            (
                user_id,
                _db_datetime(database, start),
                _db_datetime(database, end),
            ),
        ).fetchone()
    return int(row["total"] or 0) if row else 0


def _apply_floors(
    database: Any,
    user_id: int,
    period_key: str,
    floors: dict[str, int],
) -> None:
    if not floors:
        return
    assignments: list[str] = []
    parameters: list[object] = []
    for field in PERSISTED_FIELDS:
        if field not in floors:
            continue
        assignments.append(
            f"{field} = CASE WHEN {field} < ? THEN ? ELSE {field} END"
        )
        parameters.extend((floors[field], floors[field]))
    if not assignments:
        return
    parameters.extend((user_id, period_key))
    statement = database._sql(
        "UPDATE usage_counters SET "
        + ", ".join(assignments)
        + ", updated_at = CURRENT_TIMESTAMP "
        + "WHERE user_id = ? AND period_key = ?"
    )
    with database._connect() as connection:
        connection.execute(statement, tuple(parameters))
        connection.commit()


def _reconcile(
    database: Any,
    user_id: int,
    original_snapshot: Callable[[Any, int], dict[str, Any]],
) -> dict[str, Any]:
    snapshot = original_snapshot(database, user_id)
    if snapshot.get("unlimited") or not snapshot.get("active"):
        return snapshot

    access = dict(snapshot.get("access") or {})
    start = _period_start(database, user_id, access)
    end = _as_datetime(database, access.get("ends_at"))
    period_key = str(snapshot.get("period_key") or "")
    if start is None or end is None or not period_key:
        return snapshot

    totals = dict(snapshot.get("totals") or {})
    used = dict(snapshot.get("used") or {})
    floors: dict[str, int] = {}
    for field in PERSISTED_FIELDS:
        actual = _count_persisted(database, field, user_id, start, end)
        floor = min(actual, int(totals.get(field, 0)))
        if floor > int(used.get(field, 0)):
            floors[field] = floor

    if not floors:
        return snapshot
    _apply_floors(database, user_id, period_key, floors)
    return original_snapshot(database, user_id)


def install_usage_integrity(database_class: type[Any]) -> None:
    """Reconcile persistent lead/radar usage with quota counters."""
    if getattr(database_class, "_usage_integrity_installed", False):
        return

    original_snapshot = database_class.get_usage_snapshot
    original_consume = database_class.consume_usage
    original_save_leads = database_class.save_leads

    @wraps(original_snapshot)
    def get_usage_snapshot(self: Any, user_id: int) -> dict[str, Any]:
        try:
            return _reconcile(self, user_id, original_snapshot)
        except Exception:
            logging.exception("Usage counter reconciliation failed")
            return original_snapshot(self, user_id)

    @wraps(original_consume)
    def consume_usage(
        self: Any,
        user_id: int,
        field: str,
        amount: int = 1,
    ):
        try:
            _reconcile(self, user_id, original_snapshot)
        except Exception:
            logging.exception("Usage reconciliation before consume failed")
        return original_consume(self, user_id, field, amount)

    @wraps(original_save_leads)
    def save_leads(
        self: Any,
        user_id: int,
        leads: list[Any],
        project_id: int | None = None,
    ):
        try:
            _reconcile(self, user_id, original_snapshot)
        except Exception:
            logging.exception("Usage reconciliation before lead save failed")
        return original_save_leads(self, user_id, leads, project_id)

    database_class.get_usage_snapshot = get_usage_snapshot
    database_class.consume_usage = consume_usage
    database_class.save_leads = save_leads
    database_class.reconcile_usage_snapshot = get_usage_snapshot
    database_class._usage_integrity_installed = True
