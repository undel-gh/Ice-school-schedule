from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import CoachProfile, Student, StudentAccess
from audit.models import AuditEvent

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
