from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import Student
from audit.models import AuditEvent
from core.time import school_date
from scheduling.models import GroupMembership, TrainingGroup

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
