from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student, StudentAccess
from audit.models import AuditEvent
from core.time import make_school_aware, school_date
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import generate_lessons, reassign_lesson_coach

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="school-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_manager_creates_and_updates_student(client, manager):
    client.force_login(manager)

    created = client.post(
        reverse("accounts_manager:student_create"),
        {"display_name": "Маша", "is_active": "on"},
    )
    assert created.status_code == 302
    student = Student.objects.get(display_name="Маша")
    assert AuditEvent.objects.filter(
        event_type="StudentCreated",
        aggregate_id=student.id,
    ).exists()

    updated = client.post(
        reverse(
            "accounts_manager:student_edit",
            kwargs={"student_id": student.id},
        ),
        {"display_name": "Мария", "is_active": ""},
    )
    assert updated.status_code == 302
    student.refresh_from_db()
    assert student.display_name == "Мария"
    assert student.is_active is False
    assert AuditEvent.objects.filter(
        event_type="StudentChanged",
        aggregate_id=student.id,
    ).exists()


@pytest.mark.django_db
def test_manager_creates_and_disables_student_access(client, manager):
    student = Student.objects.create(display_name="Ученик")
    guardian = User.objects.create_user(
        username="guardian",
        password="test",
        email="guardian@example.test",
    )
    client.force_login(manager)

    created = client.post(
        reverse(
            "accounts_manager:student_access_create",
            kwargs={"student_id": student.id},
        ),
        {
            "user": str(guardian.id),
            "role": StudentAccess.Role.GUARDIAN,
            "is_active": "on",
        },
    )
    assert created.status_code == 302
    access = StudentAccess.objects.get(student=student, user=guardian)
    assert access.is_active is True

    updated = client.post(
        reverse(
            "accounts_manager:student_access_edit",
            kwargs={"access_id": access.id},
        ),
        {
            "role": StudentAccess.Role.GUARDIAN,
            "is_active": "",
        },
    )
    assert updated.status_code == 302
    access.refresh_from_db()
    assert access.is_active is False
    assert AuditEvent.objects.filter(
        event_type="StudentAccessChanged",
        aggregate_id=access.id,
    ).exists()


@pytest.mark.django_db
def test_manager_cannot_create_duplicate_student_access(client, manager):
    student = Student.objects.create(display_name="Ученик")
    guardian = User.objects.create_user(username="guardian-dup", password="test")
    StudentAccess.objects.create(
        student=student,
        user=guardian,
        role=StudentAccess.Role.GUARDIAN,
        is_active=False,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "accounts_manager:student_access_create",
            kwargs={"student_id": student.id},
        ),
        {
            "user": str(guardian.id),
            "role": StudentAccess.Role.GUARDIAN,
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    assert StudentAccess.objects.filter(student=student, user=guardian).count() == 1


@pytest.mark.django_db
def test_manager_creates_and_updates_coach_profile(client, manager):
    user = User.objects.create_user(username="coach-user", password="test")
    client.force_login(manager)

    created = client.post(
        reverse("accounts_manager:coach_create"),
        {
            "user": str(user.id),
            "display_name": "Анна Тренер",
            "is_active": "on",
        },
    )
    assert created.status_code == 302
    coach = CoachProfile.objects.get(user=user)
    assert coach.display_name == "Анна Тренер"

    updated = client.post(
        reverse(
            "accounts_manager:coach_edit",
            kwargs={"coach_id": coach.id},
        ),
        {
            "display_name": "Анна",
            "is_active": "",
        },
    )
    assert updated.status_code == 302
    coach.refresh_from_db()
    assert coach.display_name == "Анна"
    assert coach.is_active is False
    assert AuditEvent.objects.filter(
        event_type="CoachProfileChanged",
        aggregate_id=coach.id,
    ).exists()


@pytest.mark.django_db
def test_student_list_searches_display_name(client, manager):
    Student.objects.create(display_name="Мария")
    Student.objects.create(display_name="Пётр")
    client.force_login(manager)

    response = client.get(
        reverse("accounts_manager:students"),
        {"q": "Мари"},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "Мария" in body
    assert "Пётр" not in body


@pytest.mark.django_db
def test_manager_can_edit_access_when_linked_user_is_inactive(client, manager):
    student = Student.objects.create(display_name="Ученик")
    user = User.objects.create_user(
        username="inactive-guardian",
        password="test",
        is_active=False,
    )
    access = StudentAccess.objects.create(
        student=student,
        user=user,
        role=StudentAccess.Role.GUARDIAN,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "accounts_manager:student_access_edit",
            kwargs={"access_id": access.id},
        ),
        {
            "role": StudentAccess.Role.GUARDIAN,
            "is_active": "",
        },
    )

    assert response.status_code == 302
    access.refresh_from_db()
    assert access.is_active is False


@pytest.mark.django_db
def test_manager_can_edit_coach_when_linked_user_is_inactive(client, manager):
    user = User.objects.create_user(
        username="inactive-coach-user",
        password="test",
        is_active=False,
    )
    coach = CoachProfile.objects.create(
        user=user,
        display_name="Тренер",
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "accounts_manager:coach_edit",
            kwargs={"coach_id": coach.id},
        ),
        {
            "display_name": "Бывший тренер",
            "is_active": "",
        },
    )

    assert response.status_code == 302
    coach.refresh_from_db()
    assert coach.display_name == "Бывший тренер"
    assert coach.is_active is False


@pytest.mark.django_db
def test_scoped_student_manager_uses_operations_dashboard(client):
    manager = User.objects.create_user(
        username="student-view-manager",
        password="test",
        is_staff=True,
    )
    manager.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="accounts",
            codename="view_student",
        )
    )
    client.force_login(manager)

    home = client.get(reverse("scheduling:home"))
    assert home.status_code == 302
    assert home.url == reverse("subscriptions:manager_operations")

    dashboard = client.get(reverse("subscriptions:manager_operations"))
    body = dashboard.content.decode()
    assert dashboard.status_code == 200
    assert "Ученики и доступы" in body
    assert "Абонементы" not in body


def _coach_schedule_refs(*, coach, suffix):
    group = TrainingGroup.objects.create(
        code=f"coach-group-{suffix}",
        name=f"Coach group {suffix}",
    )
    venue = Venue.objects.create(
        code=f"coach-venue-{suffix}",
        name=f"Coach venue {suffix}",
    )
    lesson_type = LessonType.objects.create(
        code=f"coach-ice-{suffix}",
        name=f"Coach ice {suffix}",
        subscription_category="ice",
    )
    return group, venue, lesson_type


@pytest.mark.django_db
def test_manager_cannot_deactivate_coach_with_active_template(client, manager):
    user = User.objects.create_user(username="guard-coach-template", password="test")
    coach = CoachProfile.objects.create(
        user=user,
        display_name="Guard coach",
    )
    group, venue, lesson_type = _coach_schedule_refs(
        coach=coach,
        suffix="template",
    )
    today = school_date(timezone.now())
    ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=today.weekday(),
        start_time=timezone.now().time().replace(tzinfo=None),
        duration_minutes=60,
        valid_from=today,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "accounts_manager:coach_edit",
            kwargs={"coach_id": coach.id},
        ),
        {"display_name": coach.display_name, "is_active": ""},
    )

    assert response.status_code == 200
    coach.refresh_from_db()
    assert coach.is_active is True
    assert "schedule template" in response.content.decode().lower()


@pytest.mark.django_db
def test_manager_cannot_deactivate_coach_with_future_lesson(client, manager):
    user = User.objects.create_user(username="guard-coach-lesson", password="test")
    coach = CoachProfile.objects.create(
        user=user,
        display_name="Guard lesson coach",
    )
    group, venue, lesson_type = _coach_schedule_refs(
        coach=coach,
        suffix="lesson",
    )
    starts_at = timezone.now() + timedelta(days=7)
    Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "accounts_manager:coach_edit",
            kwargs={"coach_id": coach.id},
        ),
        {"display_name": coach.display_name, "is_active": ""},
    )

    assert response.status_code == 200
    coach.refresh_from_db()
    assert coach.is_active is True
    assert "future" in response.content.decode().lower()


@pytest.mark.django_db
def test_user_choice_does_not_expose_email(client, manager):
    guardian = User.objects.create_user(
        username="private-guardian",
        password="test",
        first_name="Private",
        last_name="Guardian",
        email="private@example.test",
    )
    student = Student.objects.create(display_name="Student")
    client.force_login(manager)

    response = client.get(
        reverse(
            "accounts_manager:student_access_create",
            kwargs={"student_id": student.id},
        )
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "Private Guardian" in body
    assert "private@example.test" not in body


@pytest.mark.django_db
def test_student_list_is_paginated_without_silent_truncation(client, manager):
    for index in range(55):
        Student.objects.create(display_name=f"Page student {index:03d}")
    client.force_login(manager)

    first = client.get(reverse("accounts_manager:students"))
    second = client.get(reverse("accounts_manager:students"), {"page": "2"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.context["page_obj"].paginator.count >= 55
    assert len(first.context["students"]) == 50
    assert len(second.context["students"]) >= 5
    assert "Показано" in first.content.decode()


@pytest.mark.django_db
def test_reassigning_materialized_lesson_allows_old_coach_deactivation(
    client,
    manager,
):
    old_user = User.objects.create_user(
        username="departing-coach",
        password="test",
    )
    old_coach = CoachProfile.objects.create(
        user=old_user,
        display_name="Departing Coach",
    )
    new_user = User.objects.create_user(
        username="incoming-coach",
        password="test",
    )
    new_coach = CoachProfile.objects.create(
        user=new_user,
        display_name="Incoming Coach",
    )
    group = TrainingGroup.objects.create(
        code="departure-group",
        name="Departure group",
    )
    venue = Venue.objects.create(
        code="departure-venue",
        name="Departure venue",
    )
    lesson_type = LessonType.objects.create(
        code="departure-ice",
        name="Departure ice",
        subscription_category="ice",
    )
    now = timezone.now()
    today = school_date(now)
    occurrence_date = today + timedelta(days=1)
    starts_at = make_school_aware(
        datetime.combine(occurrence_date, time(18, 0))
    )
    old_template = ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=old_coach,
        venue=venue,
        weekday=occurrence_date.weekday(),
        start_time=time(18, 0),
        duration_minutes=60,
        valid_from=today - timedelta(days=6),
        valid_until=occurrence_date,
        is_active=True,
    )
    Lesson.objects.create(
        source_template=old_template,
        group=group,
        lesson_type=lesson_type,
        coach=old_coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    next_date = occurrence_date + timedelta(days=1)
    ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=new_coach,
        venue=venue,
        weekday=next_date.weekday(),
        start_time=time(18, 0),
        duration_minutes=60,
        valid_from=next_date,
        is_active=True,
    )
    lesson = Lesson.objects.get(source_template=old_template)

    reassign_lesson_coach(
        lesson_id=lesson.id,
        coach_id=new_coach.id,
        actor=manager,
        reason="Постоянная замена тренера",
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "accounts_manager:coach_edit",
            kwargs={"coach_id": old_coach.id},
        ),
        {
            "display_name": old_coach.display_name,
            "is_active": "",
        },
    )

    assert response.status_code == 302
    old_coach.refresh_from_db()
    lesson.refresh_from_db()
    assert old_coach.is_active is False
    assert lesson.coach_id == new_coach.id

    generated = generate_lessons(
        template_id=old_template.id,
        from_date=occurrence_date,
        until_date=occurrence_date,
        actor=manager,
    )
    assert generated.lessons == (lesson,)
