from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Lead,
    LeadActivity,
    LeadWorkspace,
    NicheCategory,
    NicheProfile,
    ProjectLead,
    User,
    UserProject,
)
from app.services.pipeline_analytics_service import (
    build_pipeline_recommendations,
    calculate_pipeline_analytics,
    export_pipeline_analytics_csv,
)


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def create_user(db, name="User"):
    user = User(
        public_id=str(uuid4()),
        display_name=name,
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_project(db, user, name):
    category = NicheCategory(
        code=f"cat-{uuid4().hex[:8]}",
        name="Категория",
        emoji="📦",
        sort_order=1,
        is_active=True,
    )
    db.add(category)
    db.flush()

    profile = NicheProfile(
        category_id=category.id,
        code=f"profile-{uuid4().hex[:8]}",
        name="Профиль",
        description="",
        seller_label="Специалист",
        target_label="Компания",
        config_json="{}",
        is_custom=False,
        is_active=True,
    )
    db.add(profile)
    db.flush()

    project = UserProject(
        user_id=user.id,
        niche_profile_id=profile.id,
        name=name,
        status="active",
        summary_json="{}",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def add_lead(
    db,
    *,
    user,
    project,
    status,
    score,
    name=None,
    follow_up_at=None,
):
    lead = Lead(
        name=name or f"Лид {uuid4().hex[:6]}",
        niche="B2B",
        city="Москва",
        description="Компания",
        score=score,
        source_url=f"https://example.com/{uuid4().hex}",
        source_type="website",
        status="new",
    )
    db.add(lead)
    db.flush()

    project_lead = ProjectLead(
        user_id=user.id,
        project_id=project.id,
        lead_id=lead.id,
        status=status,
        found_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(project_lead)
    db.flush()

    if follow_up_at is not None:
        db.add(
            LeadWorkspace(
                user_id=user.id,
                project_lead_id=project_lead.id,
                note="",
                next_follow_up_at=follow_up_at,
            )
        )

    db.commit()
    return project_lead


def test_empty_pipeline_has_zero_metrics(db):
    user = create_user(db)
    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
    )

    assert analytics.total == 0
    assert analytics.average_score == 0.0
    assert analytics.contact_rate == 0.0
    assert analytics.response_rate == 0.0
    assert analytics.projects == ()


def test_funnel_conversion_math(db):
    user = create_user(db)
    project = create_project(db, user, "Основной проект")

    add_lead(db, user=user, project=project, status="found", score=50)
    add_lead(db, user=user, project=project, status="contacted", score=70)
    add_lead(db, user=user, project=project, status="replied", score=80)
    add_lead(db, user=user, project=project, status="qualified", score=90)
    add_lead(db, user=user, project=project, status="won", score=100)
    add_lead(db, user=user, project=project, status="lost", score=60)

    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
    )

    assert analytics.total == 6
    assert analytics.average_score == 75.0
    assert analytics.contact_rate == 83.3
    assert analytics.response_rate == 60.0
    assert analytics.qualification_rate == 66.7
    assert analytics.closed_win_rate == 50.0


def test_project_breakdown_is_separate(db):
    user = create_user(db)
    first = create_project(db, user, "AI")
    second = create_project(db, user, "SEO")

    add_lead(db, user=user, project=first, status="won", score=95)
    add_lead(db, user=user, project=first, status="replied", score=85)
    add_lead(db, user=user, project=second, status="found", score=55)

    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
    )

    assert len(analytics.projects) == 2
    assert analytics.projects[0].project_name == "AI"
    assert analytics.projects[0].total == 2
    assert analytics.projects[0].average_score == 90.0

    seo = next(
        project
        for project in analytics.projects
        if project.project_name == "SEO"
    )
    assert seo.total == 1
    assert seo.status_counts["found"] == 1


def test_overdue_followups_exclude_closed_leads(db):
    user = create_user(db)
    project = create_project(db, user, "Проект")
    past = datetime.utcnow() - timedelta(days=1)

    add_lead(
        db,
        user=user,
        project=project,
        status="contacted",
        score=80,
        follow_up_at=past,
    )
    add_lead(
        db,
        user=user,
        project=project,
        status="won",
        score=90,
        follow_up_at=past,
    )
    add_lead(
        db,
        user=user,
        project=project,
        status="lost",
        score=60,
        follow_up_at=past,
    )

    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
    )
    assert analytics.overdue_followups == 1


def test_activity_periods_are_counted(db):
    user = create_user(db)
    project = create_project(db, user, "Проект")
    project_lead = add_lead(
        db,
        user=user,
        project=project,
        status="contacted",
        score=80,
    )
    now = datetime.utcnow()

    db.add_all(
        [
            LeadActivity(
                user_id=user.id,
                project_lead_id=project_lead.id,
                activity_type="status_changed",
                new_value="contacted",
                created_at=now - timedelta(days=2),
            ),
            LeadActivity(
                user_id=user.id,
                project_lead_id=project_lead.id,
                activity_type="note_changed",
                new_value="note",
                created_at=now - timedelta(days=15),
            ),
            LeadActivity(
                user_id=user.id,
                project_lead_id=project_lead.id,
                activity_type="note_changed",
                new_value="old",
                created_at=now - timedelta(days=40),
            ),
        ]
    )
    db.commit()

    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
        now=now,
    )
    assert analytics.activities_7d == 1
    assert analytics.activities_30d == 2


def test_analytics_isolated_by_user(db):
    first_user = create_user(db, "First")
    second_user = create_user(db, "Second")
    first_project = create_project(db, first_user, "First Project")
    second_project = create_project(db, second_user, "Second Project")

    add_lead(
        db,
        user=first_user,
        project=first_project,
        status="won",
        score=100,
    )
    add_lead(
        db,
        user=second_user,
        project=second_project,
        status="found",
        score=40,
    )

    first = calculate_pipeline_analytics(
        db,
        user_id=first_user.id,
    )
    second = calculate_pipeline_analytics(
        db,
        user_id=second_user.id,
    )

    assert first.total == 1
    assert first.status_counts["won"] == 1
    assert second.status_counts["found"] == 1
    assert first.average_score == 100.0
    assert second.average_score == 40.0


def test_recommendations_flag_low_activity(db):
    user = create_user(db)
    project = create_project(db, user, "Проект")

    for _ in range(6):
        add_lead(
            db,
            user=user,
            project=project,
            status="found",
            score=55,
        )

    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
    )
    recommendations = build_pipeline_recommendations(analytics)

    assert any("Разберите новые лиды" in item for item in recommendations)
    assert any("Низкая доля контактов" in item for item in recommendations)


def test_csv_export_has_bom_and_projects(db):
    user = create_user(db)
    project = create_project(db, user, "Проект Ёлка")
    add_lead(
        db,
        user=user,
        project=project,
        status="won",
        score=91,
    )

    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
    )
    content = export_pipeline_analytics_csv(analytics)

    assert content.startswith(b"\xef\xbb\xbf")
    decoded = content.decode("utf-8-sig")
    assert "Показатель;Значение" in decoded
    assert "Проект Ёлка" in decoded
    assert "Средний рейтинг" in decoded


def test_closed_win_rate_ignores_open_leads(db):
    user = create_user(db)
    project = create_project(db, user, "Проект")

    add_lead(db, user=user, project=project, status="found", score=50)
    add_lead(db, user=user, project=project, status="contacted", score=70)
    add_lead(db, user=user, project=project, status="won", score=90)
    add_lead(db, user=user, project=project, status="lost", score=60)
    add_lead(db, user=user, project=project, status="lost", score=60)

    analytics = calculate_pipeline_analytics(
        db,
        user_id=user.id,
    )
    assert analytics.closed_win_rate == 33.3
