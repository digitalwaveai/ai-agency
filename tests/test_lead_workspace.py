from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
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
from app.services.lead_workspace_service import (
    LeadWorkspaceError,
    export_pipeline_csv,
    get_pipeline_lead,
    list_due_follow_ups,
    list_pipeline_leads,
    pipeline_counts,
    save_lead_note,
    schedule_follow_up,
    update_pipeline_status,
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


def create_pipeline_lead(db, user, *, lead_name="Компания Альфа"):
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
        name="Проект продаж",
        status="active",
        summary_json="{}",
    )
    db.add(project)
    db.flush()

    lead = Lead(
        name=lead_name,
        niche="B2B",
        city="Москва",
        phone="+79990000000",
        email="hello@example.com",
        description="Заявки обрабатываются вручную.",
        pain_points=(
            "Заявки обрабатываются вручную\n"
            "Подтверждение: «Заявки обрабатываются вручную»"
        ),
        suggested_offer="Автоматизация заявок",
        score=88,
        source_url="https://example.com/company",
        source_type="website",
        status="new",
    )
    db.add(lead)
    db.flush()

    project_lead = ProjectLead(
        user_id=user.id,
        project_id=project.id,
        lead_id=lead.id,
        status="found",
        found_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(project_lead)
    db.commit()
    db.refresh(project_lead)
    return project_lead, lead, project


def test_status_update_creates_workspace_and_activity(db):
    user = create_user(db)
    project_lead, _, _ = create_pipeline_lead(db, user)

    row = update_pipeline_status(
        db,
        user_id=user.id,
        project_lead_id=project_lead.id,
        status="contacted",
    )

    assert row.project_lead.status == "contacted"
    assert row.workspace is not None
    assert row.workspace.last_contacted_at is not None
    assert db.scalar(select(func.count(LeadActivity.id))) == 1


def test_note_and_follow_up_are_saved(db):
    user = create_user(db)
    project_lead, _, _ = create_pipeline_lead(db, user)
    follow_up = datetime.utcnow() + timedelta(days=2)

    row = save_lead_note(
        db,
        user_id=user.id,
        project_lead_id=project_lead.id,
        note="Отправить предложение после созвона",
    )
    assert row.workspace.note == "Отправить предложение после созвона"

    row = schedule_follow_up(
        db,
        user_id=user.id,
        project_lead_id=project_lead.id,
        follow_up_at=follow_up,
    )
    assert row.workspace.next_follow_up_at == follow_up
    assert db.scalar(select(func.count(LeadActivity.id))) == 2


def test_user_cannot_edit_another_users_lead(db):
    owner = create_user(db, "Owner")
    stranger = create_user(db, "Stranger")
    project_lead, _, _ = create_pipeline_lead(db, owner)

    with pytest.raises(LeadWorkspaceError):
        update_pipeline_status(
            db,
            user_id=stranger.id,
            project_lead_id=project_lead.id,
            status="won",
        )


def test_pipeline_counts_and_filters(db):
    user = create_user(db)
    first, _, _ = create_pipeline_lead(db, user, lead_name="Первый")
    second, _, _ = create_pipeline_lead(db, user, lead_name="Второй")

    update_pipeline_status(
        db,
        user_id=user.id,
        project_lead_id=first.id,
        status="contacted",
    )

    counts = pipeline_counts(db, user_id=user.id)
    assert counts["contacted"] == 1
    assert counts["found"] == 1

    rows = list_pipeline_leads(
        db,
        user_id=user.id,
        status="contacted",
    )
    assert len(rows) == 1
    assert rows[0].project_lead.id == first.id
    assert second.id != first.id


def test_due_follow_ups_exclude_closed_leads(db):
    user = create_user(db)
    due, _, _ = create_pipeline_lead(db, user, lead_name="Просроченный")
    won, _, _ = create_pipeline_lead(db, user, lead_name="Закрытый")

    past = datetime.utcnow() - timedelta(hours=2)
    schedule_follow_up(
        db,
        user_id=user.id,
        project_lead_id=due.id,
        follow_up_at=past,
    )
    schedule_follow_up(
        db,
        user_id=user.id,
        project_lead_id=won.id,
        follow_up_at=past,
    )
    update_pipeline_status(
        db,
        user_id=user.id,
        project_lead_id=won.id,
        status="won",
    )

    rows = list_due_follow_ups(db, user_id=user.id)
    assert [row.project_lead.id for row in rows] == [due.id]


def test_csv_export_is_excel_compatible_utf8(db):
    user = create_user(db)
    project_lead, _, _ = create_pipeline_lead(
        db,
        user,
        lead_name="Компания Ёлка",
    )
    save_lead_note(
        db,
        user_id=user.id,
        project_lead_id=project_lead.id,
        note="Связаться в понедельник",
    )

    content = export_pipeline_csv(
        list_pipeline_leads(db, user_id=user.id)
    )

    assert content.startswith(b"\xef\xbb\xbf")
    decoded = content.decode("utf-8-sig")
    assert "Проект;Лид;Статус" in decoded
    assert "Компания Ёлка" in decoded
    assert "Связаться в понедельник" in decoded


def test_invalid_status_is_rejected(db):
    user = create_user(db)
    project_lead, _, _ = create_pipeline_lead(db, user)

    with pytest.raises(LeadWorkspaceError):
        update_pipeline_status(
            db,
            user_id=user.id,
            project_lead_id=project_lead.id,
            status="unknown",
        )


def test_get_pipeline_lead_returns_joined_data(db):
    user = create_user(db)
    project_lead, lead, project = create_pipeline_lead(db, user)

    row = get_pipeline_lead(
        db,
        user_id=user.id,
        project_lead_id=project_lead.id,
    )

    assert row.lead.id == lead.id
    assert row.project.id == project.id
    assert row.project_lead.id == project_lead.id
    assert row.workspace is None


def test_workspace_tables_exist(db):
    assert "lead_workspaces" in Base.metadata.tables
    assert "lead_activities" in Base.metadata.tables
    assert db.scalar(select(func.count(LeadWorkspace.id))) == 0
