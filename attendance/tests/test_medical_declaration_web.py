from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student, StudentAccess
from attendance.models import AbsenceJustification, Attendance
from audit.models import AuditEvent
from scheduling.models import (
    Lesson,
    LessonRosterEntry,
    LessonType,
    TrainingGroup,
    Venue,
)

User = get_user_model()


def _make_context(*, attendance_status: str):
    parent = User.objects.create_user(
        username=f"medical-parent-{attendance_status}",
        password="test",
    )
    coach_user = User.objects.create_user(
        username=f"medical-coach-{attendance_status}",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Medical Coach",
    )
    student = Student.objects.create(display_name="Medical Student")
    StudentAccess.objects.create(
        user=parent,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    group = TrainingGroup.objects.create(
        code=f"medical-web-group-{attendance_status}",
        name="Medical Web Group",
    )
    venue = Venue.objects.create(
        code=f"medical-web-venue-{attendance_status}",
        name="Medical Web Venue",
    )
    lesson_type = LessonType.objects.create(
        code=f"medical-web-ice-{attendance_status}",
        name="Лёд",
        subscription_category="ice",
    )
    starts_at = timezone.now() - timedelta(days=2)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.COMPLETED,
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=attendance_status,
        marked_at=lesson.ends_at,
        marked_by=coach_user,
        updated_by=coach_user,
    )
    return parent, student, lesson, attendance


@pytest.mark.django_db
def test_absent_student_can_declare_medical_absence_from_schedule(client):
    parent, student, lesson, _attendance = _make_context(
        attendance_status=Attendance.Status.ABSENT,
    )
    client.force_login(parent)

    schedule = client.get(
        reverse("scheduling:student_schedule"),
        {
            "student": str(student.id),
            "from": (timezone.localdate() - timedelta(days=5)).isoformat(),
            "until": timezone.localdate().isoformat(),
        },
    )
    assert schedule.status_code == 200
    assert "Заявить медицинское отсутствие" in schedule.content.decode()

    response = client.post(
        reverse(
            "scheduling:declare_medical_absence",
            kwargs={
                "student_id": student.id,
                "lesson_id": lesson.id,
            },
        ),
        follow=True,
    )

    assert response.status_code == 200
    justification = AbsenceJustification.objects.get(
        student=student,
        lesson=lesson,
        type=AbsenceJustification.Type.MEDICAL,
    )
    assert justification.status == AbsenceJustification.Status.PENDING
    assert justification.declared_by_id == parent.id
    assert "ожидает проверки менеджером" in response.content.decode()
    assert AuditEvent.objects.filter(
        event_type="AbsenceJustificationDeclared",
        aggregate_id=justification.id,
        actor=parent,
    ).exists()


@pytest.mark.django_db
def test_declared_medical_absence_appears_in_manager_queue(client):
    parent, student, lesson, _attendance = _make_context(
        attendance_status=Attendance.Status.ABSENT,
    )
    client.force_login(parent)
    client.post(
        reverse(
            "scheduling:declare_medical_absence",
            kwargs={
                "student_id": student.id,
                "lesson_id": lesson.id,
            },
        )
    )

    manager = User.objects.create_superuser(
        username="medical-web-manager",
        password="test",
    )
    client.force_login(manager)
    response = client.get(
        reverse("attendance_manager:medical"),
        {"status": AbsenceJustification.Status.PENDING},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert student.display_name in body
    assert lesson.lesson_type.name in body


@pytest.mark.django_db
def test_medical_absence_declaration_rejects_present_attendance(client):
    parent, student, lesson, _attendance = _make_context(
        attendance_status=Attendance.Status.PRESENT,
    )
    client.force_login(parent)

    response = client.post(
        reverse(
            "scheduling:declare_medical_absence",
            kwargs={
                "student_id": student.id,
                "lesson_id": lesson.id,
            },
        ),
        follow=True,
    )

    assert response.status_code == 200
    assert not AbsenceJustification.objects.filter(
        student=student,
        lesson=lesson,
    ).exists()
    assert "медицин" in response.content.decode().lower()


@pytest.mark.django_db
def test_medical_absence_declaration_is_idempotent_while_pending(client):
    parent, student, lesson, _attendance = _make_context(
        attendance_status=Attendance.Status.ABSENT,
    )
    client.force_login(parent)
    url = reverse(
        "scheduling:declare_medical_absence",
        kwargs={
            "student_id": student.id,
            "lesson_id": lesson.id,
        },
    )

    first = client.post(url)
    second = client.post(url)

    assert first.status_code == 302
    assert second.status_code == 302
    assert AbsenceJustification.objects.filter(
        student=student,
        lesson=lesson,
        type=AbsenceJustification.Type.MEDICAL,
        status=AbsenceJustification.Status.PENDING,
    ).count() == 1
    assert AuditEvent.objects.filter(
        event_type="AbsenceJustificationDeclared",
        actor=parent,
    ).count() == 1


@pytest.mark.django_db
def test_medical_absence_declaration_requires_student_access_before_lookup(client):
    outsider = User.objects.create_user(
        username="medical-web-outsider",
        password="test",
    )
    client.force_login(outsider)

    response = client.post(
        reverse(
            "scheduling:declare_medical_absence",
            kwargs={
                "student_id": uuid4(),
                "lesson_id": uuid4(),
            },
        )
    )

    assert response.status_code == 403
