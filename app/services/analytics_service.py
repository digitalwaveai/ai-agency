from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminNotification, AnalyticsEvent, SearchRun


SENSITIVE_KEYS = {
    "token",
    "password",
    "secret",
    "api_key",
    "authorization",
    "cookie",
    "card",
    "cvv",
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            result[str(key)] = "***" if normalized in SENSITIVE_KEYS else _sanitize(item)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return value


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_sanitize(value), ensure_ascii=False, sort_keys=True, default=str)


def log_analytics_event(
    db: Session,
    *,
    platform: str,
    event_name: str,
    user_id: int | None = None,
    external_user_id: str | int | None = None,
    username: str | None = None,
    plan_code: str | None = None,
    command_name: str | None = None,
    parameters: dict[str, Any] | None = None,
    status: str = "success",
    duration_ms: int | None = None,
    result_count: int | None = None,
    error_message: str | None = None,
    guild_id: str | int | None = None,
    channel_id: str | int | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> AnalyticsEvent:
    now = now or datetime.utcnow()
    event = AnalyticsEvent(
        user_id=user_id,
        platform=platform.strip().lower(),
        external_user_id=None if external_user_id is None else str(external_user_id),
        username=username,
        plan_code=plan_code,
        event_name=event_name.strip().lower(),
        command_name=command_name,
        parameters_json=_json_dumps(parameters),
        status=status,
        duration_ms=duration_ms,
        result_count=result_count,
        error_message=None if error_message is None else error_message[:1000],
        guild_id=None if guild_id is None else str(guild_id),
        channel_id=None if channel_id is None else str(channel_id),
        session_id=session_id,
        created_at=now,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def start_search_run(
    db: Session,
    *,
    platform: str,
    user_id: int | None,
    niche: str | None,
    city: str | None,
    parameters: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> SearchRun:
    now = now or datetime.utcnow()
    run = SearchRun(
        user_id=user_id,
        platform=platform.strip().lower(),
        niche=niche,
        city=city,
        parameters_json=_json_dumps(parameters),
        status="running",
        result_count=0,
        started_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_search_run(
    db: Session,
    *,
    search_run_id: int,
    status: str,
    result_count: int = 0,
    duration_ms: int | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> SearchRun:
    now = now or datetime.utcnow()
    run = db.get(SearchRun, search_run_id)
    if run is None:
        raise ValueError("Запуск поиска не найден")
    if run.status != "running":
        return run

    run.status = status
    run.result_count = max(0, result_count)
    run.duration_ms = duration_ms
    run.error_message = None if error_message is None else error_message[:1000]
    run.finished_at = now
    db.commit()
    db.refresh(run)
    return run


def queue_admin_notification(
    db: Session,
    *,
    notification_type: str,
    payload: dict[str, Any] | None = None,
    user_id: int | None = None,
    payment_id: int | None = None,
    idempotency_key: str | None = None,
    available_at: datetime | None = None,
) -> AdminNotification:
    if idempotency_key:
        existing = db.scalar(
            select(AdminNotification).where(
                AdminNotification.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing

    now = datetime.utcnow()
    notification = AdminNotification(
        user_id=user_id,
        payment_id=payment_id,
        notification_type=notification_type.strip().lower(),
        status="pending",
        idempotency_key=idempotency_key,
        payload_json=_json_dumps(payload),
        attempts=0,
        available_at=available_at or now,
        created_at=now,
        updated_at=now,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def pending_admin_notifications(
    db: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> list[AdminNotification]:
    now = now or datetime.utcnow()
    return list(
        db.scalars(
            select(AdminNotification)
            .where(
                AdminNotification.status == "pending",
                AdminNotification.available_at <= now,
            )
            .order_by(AdminNotification.created_at.asc())
            .limit(max(1, min(limit, 500)))
        ).all()
    )
