from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Lead,
    LeadActivity,
    LeadWorkspace,
    ProjectLead,
    UserProject,
)
from app.services.lead_workspace_service import PIPELINE_STATUSES


CONTACTED_OR_LATER = {
    "contacted",
    "replied",
    "qualified",
    "won",
    "lost",
}
REPLIED_OR_LATER = {
    "replied",
    "qualified",
    "won",
}
QUALIFIED_OR_LATER = {
    "qualified",
    "won",
}


@dataclass(frozen=True)
class ProjectPipelineMetrics:
    project_id: int
    project_name: str
    total: int
    status_counts: dict[str, int]
    average_score: float
    contact_rate: float
    response_rate: float
    qualification_rate: float
    closed_win_rate: float


@dataclass(frozen=True)
class PipelineAnalytics:
    user_id: int
    generated_at: datetime
    total: int
    status_counts: dict[str, int]
    average_score: float
    contact_rate: float
    response_rate: float
    qualification_rate: float
    closed_win_rate: float
    overdue_followups: int
    activities_7d: int
    activities_30d: int
    projects: tuple[ProjectPipelineMetrics, ...]


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 1)


def _status_counts(rows: list[tuple[str, int]]) -> dict[str, int]:
    result = {status: 0 for status in PIPELINE_STATUSES}
    for status, count in rows:
        result[str(status)] = int(count)
    return result


def _stage_metrics(
    *,
    total: int,
    counts: dict[str, int],
) -> tuple[float, float, float, float]:
    contacted = sum(counts.get(status, 0) for status in CONTACTED_OR_LATER)
    replied = sum(counts.get(status, 0) for status in REPLIED_OR_LATER)
    qualified = sum(counts.get(status, 0) for status in QUALIFIED_OR_LATER)
    won = counts.get("won", 0)
    lost = counts.get("lost", 0)

    return (
        _percent(contacted, total),
        _percent(replied, contacted),
        _percent(qualified, replied),
        _percent(won, won + lost),
    )


def _project_metrics(
    db: Session,
    *,
    user_id: int,
) -> tuple[ProjectPipelineMetrics, ...]:
    rows = db.execute(
        select(
            UserProject.id,
            UserProject.name,
            ProjectLead.status,
            func.count(ProjectLead.id),
        )
        .join(ProjectLead, ProjectLead.project_id == UserProject.id)
        .where(ProjectLead.user_id == user_id)
        .group_by(
            UserProject.id,
            UserProject.name,
            ProjectLead.status,
        )
        .order_by(UserProject.name.asc())
    ).all()

    score_rows = db.execute(
        select(
            UserProject.id,
            func.avg(Lead.score),
        )
        .join(ProjectLead, ProjectLead.project_id == UserProject.id)
        .join(Lead, Lead.id == ProjectLead.lead_id)
        .where(ProjectLead.user_id == user_id)
        .group_by(UserProject.id)
    ).all()
    average_scores = {
        int(project_id): round(float(value or 0.0), 1)
        for project_id, value in score_rows
    }

    grouped: dict[int, dict] = {}
    for project_id, project_name, status, count in rows:
        project_id = int(project_id)
        item = grouped.setdefault(
            project_id,
            {
                "project_name": str(project_name),
                "counts": {
                    pipeline_status: 0
                    for pipeline_status in PIPELINE_STATUSES
                },
            },
        )
        item["counts"][str(status)] = int(count)

    result: list[ProjectPipelineMetrics] = []
    for project_id, item in grouped.items():
        counts = item["counts"]
        total = sum(counts.values())
        contact_rate, response_rate, qualification_rate, win_rate = (
            _stage_metrics(total=total, counts=counts)
        )
        result.append(
            ProjectPipelineMetrics(
                project_id=project_id,
                project_name=item["project_name"],
                total=total,
                status_counts=counts,
                average_score=average_scores.get(project_id, 0.0),
                contact_rate=contact_rate,
                response_rate=response_rate,
                qualification_rate=qualification_rate,
                closed_win_rate=win_rate,
            )
        )

    result.sort(
        key=lambda item: (
            item.status_counts.get("won", 0),
            item.response_rate,
            item.total,
        ),
        reverse=True,
    )
    return tuple(result)


def calculate_pipeline_analytics(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> PipelineAnalytics:
    now = now or datetime.utcnow()

    status_rows = db.execute(
        select(
            ProjectLead.status,
            func.count(ProjectLead.id),
        )
        .where(ProjectLead.user_id == user_id)
        .group_by(ProjectLead.status)
    ).all()
    counts = _status_counts(status_rows)
    total = sum(counts.values())

    average_score = db.scalar(
        select(func.avg(Lead.score))
        .join(ProjectLead, ProjectLead.lead_id == Lead.id)
        .where(ProjectLead.user_id == user_id)
    )
    average_score_value = round(float(average_score or 0.0), 1)

    overdue_followups = db.scalar(
        select(func.count(LeadWorkspace.id))
        .join(
            ProjectLead,
            ProjectLead.id == LeadWorkspace.project_lead_id,
        )
        .where(
            ProjectLead.user_id == user_id,
            LeadWorkspace.next_follow_up_at.is_not(None),
            LeadWorkspace.next_follow_up_at <= now,
            ProjectLead.status.notin_({"won", "lost"}),
        )
    )

    activities_7d = db.scalar(
        select(func.count(LeadActivity.id)).where(
            LeadActivity.user_id == user_id,
            LeadActivity.created_at >= now - timedelta(days=7),
        )
    )
    activities_30d = db.scalar(
        select(func.count(LeadActivity.id)).where(
            LeadActivity.user_id == user_id,
            LeadActivity.created_at >= now - timedelta(days=30),
        )
    )

    contact_rate, response_rate, qualification_rate, win_rate = (
        _stage_metrics(total=total, counts=counts)
    )

    return PipelineAnalytics(
        user_id=user_id,
        generated_at=now,
        total=total,
        status_counts=counts,
        average_score=average_score_value,
        contact_rate=contact_rate,
        response_rate=response_rate,
        qualification_rate=qualification_rate,
        closed_win_rate=win_rate,
        overdue_followups=int(overdue_followups or 0),
        activities_7d=int(activities_7d or 0),
        activities_30d=int(activities_30d or 0),
        projects=_project_metrics(db, user_id=user_id),
    )


def build_pipeline_recommendations(
    analytics: PipelineAnalytics,
) -> tuple[str, ...]:
    recommendations: list[str] = []

    if analytics.total == 0:
        return (
            "Создайте проект и выполните первый поиск клиентов.",
        )

    if analytics.status_counts.get("found", 0) >= 5:
        recommendations.append(
            "Разберите новые лиды: сохраните подходящих и начните контакт."
        )

    if analytics.contact_rate < 35:
        recommendations.append(
            "Низкая доля контактов. Выберите 5 лучших лидов и отправьте первые сообщения."
        )

    contacted = sum(
        analytics.status_counts.get(status, 0)
        for status in CONTACTED_OR_LATER
    )
    if contacted >= 5 and analytics.response_rate < 15:
        recommendations.append(
            "Ответов мало. Проверьте персонализацию сообщения и точность подтверждённой боли."
        )

    replied = sum(
        analytics.status_counts.get(status, 0)
        for status in REPLIED_OR_LATER
    )
    if replied >= 3 and analytics.qualification_rate < 30:
        recommendations.append(
            "Мало квалифицированных лидов. Уточните бюджет, задачу и срок на первом диалоге."
        )

    if analytics.overdue_followups:
        recommendations.append(
            f"Есть просроченные контакты: {analytics.overdue_followups}. Обработайте их сегодня."
        )

    if analytics.average_score < 60 and analytics.total >= 5:
        recommendations.append(
            "Средний рейтинг лидов ниже 60. Уточните анкету проекта и признаки целевого клиента."
        )

    if not recommendations:
        recommendations.append(
            "Воронка выглядит стабильно. Продолжайте регулярный поиск и follow-up."
        )

    return tuple(recommendations[:4])


def export_pipeline_analytics_csv(
    analytics: PipelineAnalytics,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )

    writer.writerow(["Показатель", "Значение"])
    writer.writerow(["Всего лидов", analytics.total])
    writer.writerow(["Средний рейтинг", analytics.average_score])
    writer.writerow(["Доля контактов, %", analytics.contact_rate])
    writer.writerow(["Доля ответов, %", analytics.response_rate])
    writer.writerow(["Квалификация ответивших, %", analytics.qualification_rate])
    writer.writerow(["Победы среди закрытых, %", analytics.closed_win_rate])
    writer.writerow(["Просроченные контакты", analytics.overdue_followups])
    writer.writerow(["Действия за 7 дней", analytics.activities_7d])
    writer.writerow(["Действия за 30 дней", analytics.activities_30d])
    writer.writerow([])

    writer.writerow(
        [
            "Проект",
            "Всего",
            "Средний рейтинг",
            "Новые",
            "Связались",
            "Ответили",
            "Квалифицированы",
            "Сделки",
            "Отказы",
            "Доля контактов, %",
            "Доля ответов, %",
            "Победы среди закрытых, %",
        ]
    )

    for project in analytics.projects:
        counts = project.status_counts
        writer.writerow(
            [
                project.project_name,
                project.total,
                project.average_score,
                counts.get("found", 0),
                counts.get("contacted", 0),
                counts.get("replied", 0),
                counts.get("qualified", 0),
                counts.get("won", 0),
                counts.get("lost", 0),
                project.contact_rate,
                project.response_rate,
                project.closed_win_rate,
            ]
        )

    return output.getvalue().encode("utf-8-sig")
