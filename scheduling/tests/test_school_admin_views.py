from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student
from audit.models import AuditEvent
from core.time import school_date
from scheduling.models import (
    GroupMembership,
    Lesson,
    LessonType,
    ScheduleTemplate,
    TrainingGroup,
    Venue,
)
from scheduling.school_admin_forms import GroupMembershipForm
from scheduling.services import create_group_membership

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="group-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_manager_creates_and_updates_training_group(client, manager):
    client.force_login(manager)

    created = client.post(
        reverse("school_scheduling:group_create"),
        {
            "code": "beginners",
            "name": "Начальная группа",
            "default_minimum_attendees": "3",
            "is_active": "on",
        },
    )
    assert created.status_code == 302
    group = TrainingGroup.objects.get(code="beginners")
    assert group.default_minimum_attendees == 3
    assert AuditEvent.objects.filter(
        event_type="TrainingGroupCreated",
        aggregate_id=group.id,
    ).exists()

    updated = client.post(
        reverse(
            "school_scheduling:group_edit",
            kwargs={"group_id": group.id},
        ),
        {
            "code": "beginners",
            "name": "Начальная 1",
            "default_minimum_attendees": "4",
            "is_active": "",
        },
    )
    assert updated.status_code == 302
    group.refresh_from_db()
    assert group.name == "Начальная 1"
    assert group.default_minimum_attendees == 4
    assert group.is_active is False


@pytest.mark.django_db
def test_manager_creates_and_ends_group_membership(client, manager):
    student = Student.objects.create(display_name="Ученик")
    group = TrainingGroup.objects.create(code="group-a", name="Группа A")
    today = school_date(timezone.now())
    client.force_login(manager)

    created = client.post(
        reverse("school_scheduling:membership_create"),
        {
            "student": str(student.id),
            "group": str(group.id),
            "starts_on": today.isoformat(),
            "ends_on": "",
        },
    )
    assert created.status_code == 302
    membership = GroupMembership.objects.get(student=student, group=group)
    assert membership.ends_on is None
    assert AuditEvent.objects.filter(
        event_type="GroupMembershipCreated",
        aggregate_id=membership.id,
    ).exists()

    end_date = today + timedelta(days=30)
    updated = client.post(
        reverse(
            "school_scheduling:membership_edit",
            kwargs={"membership_id": membership.id},
        ),
        {
            "starts_on": today.isoformat(),
            "ends_on": end_date.isoformat(),
        },
    )
    assert updated.status_code == 302
    membership.refresh_from_db()
    assert membership.ends_on == end_date
    assert AuditEvent.objects.filter(
        event_type="GroupMembershipChanged",
        aggregate_id=membership.id,
    ).exists()


@pytest.mark.django_db
def test_manager_membership_overlap_is_rejected(client, manager):
    student = Student.objects.create(display_name="Ученик")
    group = TrainingGroup.objects.create(code="group-overlap", name="Группа")
    today = school_date(timezone.now())
    GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=today,
        ends_on=today + timedelta(days=30),
        created_by=manager,
    )
    client.force_login(manager)

    response = client.post(
        reverse("school_scheduling:membership_create"),
        {
            "student": str(student.id),
            "group": str(group.id),
            "starts_on": (today + timedelta(days=10)).isoformat(),
            "ends_on": (today + timedelta(days=40)).isoformat(),
        },
    )

    assert response.status_code == 200
    assert GroupMembership.objects.filter(student=student, group=group).count() == 1
    assert "пересека" in response.content.decode().lower() or "overlap" in response.content.decode().lower()


@pytest.mark.django_db
def test_membership_list_defaults_to_current(client, manager):
    student = Student.objects.create(display_name="Ученик")
    group = TrainingGroup.objects.create(code="group-current", name="Группа")
    today = school_date(timezone.now())
    current = GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=today - timedelta(days=5),
        ends_on=None,
        created_by=manager,
    )
    ended_student = Student.objects.create(display_name="Бывший ученик")
    GroupMembership.objects.create(
        student=ended_student,
        group=group,
        starts_on=today - timedelta(days=30),
        ends_on=today - timedelta(days=1),
        created_by=manager,
    )
    client.force_login(manager)

    response = client.get(reverse("school_scheduling:memberships"))

    body = response.content.decode()
    assert response.status_code == 200
    assert current.student.display_name in body
    assert "Бывший ученик" not in body


def _schedule_refs(*, group, manager, suffix):
    coach_user = User.objects.create_user(
        username=f"coach-{suffix}",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name=f"Coach {suffix}",
    )
    venue = Venue.objects.create(
        code=f"venue-{suffix}",
        name=f"Venue {suffix}",
    )
    lesson_type = LessonType.objects.create(
        code=f"ice-{suffix}",
        name=f"Ice {suffix}",
        subscription_category="ice",
    )
    return coach, venue, lesson_type


@pytest.mark.django_db
def test_manager_cannot_deactivate_group_with_active_template(client, manager):
    group = TrainingGroup.objects.create(
        code="guard-template-group",
        name="Guard template group",
    )
    coach, venue, lesson_type = _schedule_refs(
        group=group,
        manager=manager,
        suffix="group-template",
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
            "school_scheduling:group_edit",
            kwargs={"group_id": group.id},
        ),
        {
            "code": group.code,
            "name": group.name,
            "default_minimum_attendees": "1",
            "is_active": "",
        },
    )

    assert response.status_code == 200
    group.refresh_from_db()
    assert group.is_active is True
    assert "шаблон расписания" in response.content.decode().lower()


@pytest.mark.django_db
def test_manager_cannot_deactivate_group_with_future_lesson(client, manager):
    group = TrainingGroup.objects.create(
        code="guard-lesson-group",
        name="Guard lesson group",
    )
    coach, venue, lesson_type = _schedule_refs(
        group=group,
        manager=manager,
        suffix="group-lesson",
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
            "school_scheduling:group_edit",
            kwargs={"group_id": group.id},
        ),
        {
            "code": group.code,
            "name": group.name,
            "default_minimum_attendees": "1",
            "is_active": "",
        },
    )

    assert response.status_code == 200
    group.refresh_from_db()
    assert group.is_active is True
    assert "будущие неотменённые занятия" in response.content.decode().lower()


@pytest.mark.django_db
def test_new_membership_rejects_inactive_student_and_group(manager):
    today = school_date(timezone.now())
    active_student = Student.objects.create(display_name="Active student")
    inactive_student = Student.objects.create(
        display_name="Inactive student",
        is_active=False,
    )
    active_group = TrainingGroup.objects.create(
        code="active-membership-group",
        name="Active group",
    )
    inactive_group = TrainingGroup.objects.create(
        code="inactive-membership-group",
        name="Inactive group",
        is_active=False,
    )

    with pytest.raises(ValidationError) as student_error:
        create_group_membership(
            student_id=inactive_student.id,
            group_id=active_group.id,
            starts_on=today,
            ends_on=None,
            actor=manager,
        )
    assert "inactive students" in str(student_error.value).lower()

    with pytest.raises(ValidationError) as group_error:
        create_group_membership(
            student_id=active_student.id,
            group_id=inactive_group.id,
            starts_on=today,
            ends_on=None,
            actor=manager,
        )
    assert "inactive group" in str(group_error.value).lower()


@pytest.mark.django_db
def test_new_membership_form_lists_only_active_entities():
    active_student = Student.objects.create(display_name="Active student")
    inactive_student = Student.objects.create(
        display_name="Inactive student",
        is_active=False,
    )
    active_group = TrainingGroup.objects.create(
        code="active-form-group",
        name="Active group",
    )
    inactive_group = TrainingGroup.objects.create(
        code="inactive-form-group",
        name="Inactive group",
        is_active=False,
    )

    form = GroupMembershipForm()

    assert active_student in form.fields["student"].queryset
    assert inactive_student not in form.fields["student"].queryset
    assert active_group in form.fields["group"].queryset
    assert inactive_group not in form.fields["group"].queryset


@pytest.mark.django_db
def test_group_list_is_paginated_without_silent_truncation(client, manager):
    for index in range(55):
        TrainingGroup.objects.create(
            code=f"page-group-{index:03d}",
            name=f"Page group {index:03d}",
        )
    client.force_login(manager)

    first = client.get(reverse("school_scheduling:groups"))
    second = client.get(reverse("school_scheduling:groups"), {"page": "2"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.context["page_obj"].paginator.count >= 55
    assert len(first.context["groups"]) == 50
    assert len(second.context["groups"]) >= 5
    assert "Показано" in first.content.decode()
