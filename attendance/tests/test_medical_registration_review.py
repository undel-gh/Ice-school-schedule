from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student, StudentAccess
from attendance.medical_registration import declare_medical_absence_by_manager
from attendance.models import AbsenceJustification, Attendance
from audit.models import AuditEvent
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue

User = get_user_model()


def _make_absence(*, with_guardian: bool = True, days_ago: int = 30):
    guardian = User.objects.create_user(
        username=f"medical-history-guardian-{days_ago}",
        password="test",
    )
    coach_user = User.objects.create_user(
        username=f"medical-history-coach-{days_ago}",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Тренер медицинского теста",
    )
    student = Student.objects.create(display_name="Ученик со справкой")
    if with_guardian:
        StudentAccess.objects.create(
            user=guardian,
            student=student,
            role=StudentAccess.Role.GUARDIAN,
        )
    group = TrainingGroup.objects.create(
        code=f"medical-history-group-{days_ago}",
        name="Группа медицинского теста",
    )
    venue = Venue.objects.create(
        code=f"medical-history-venue-{days_ago}",
        name="Лёд медицинского теста",
    )
    lesson_type = LessonType.objects.create(
        code=f"medical-history-type-{days_ago}",
        name="Лёд",
        subscription_category="ice",
    )
    starts_at = timezone.now() - timedelta(days=days_ago)
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
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.ABSENT,
        marked_at=lesson.ends_at,
        marked_by=coach_user,
        updated_by=coach_user,
    )
    return guardian, student, lesson, attendance


@pytest.mark.django_db
def test_old_absence_can_be_declared_from_student_account_history(client):
    guardian, student, lesson, _attendance = _make_absence(days_ago=45)
    client.force_login(guardian)

    account = client.get(
        reverse("student_account:account"),
        {"student": str(student.id)},
    )
    assert account.status_code == 200
    body = account.content.decode()
    assert "Отсутствовал" in body
    assert "Заявить медицинское отсутствие" in body

    response = client.post(
        reverse(
            "scheduling:declare_medical_absence",
            kwargs={"student_id": student.id, "lesson_id": lesson.id},
        ),
        {"return_to": "account", "page": "1"},
        follow=True,
    )

    assert response.status_code == 200
    assert response.redirect_chain[0][0].startswith(
        reverse("student_account:account")
    )
    justification = AbsenceJustification.objects.get(
        student=student,
        lesson=lesson,
        type=AbsenceJustification.Type.MEDICAL,
    )
    assert justification.status == AbsenceJustification.Status.PENDING
    body = response.content.decode()
    assert "Медицинское основание · Ожидает проверки" in body
    assert "Заявить медицинское отсутствие" not in body


@pytest.mark.django_db
def test_manager_can_register_absence_without_student_access(client):
    _guardian, student, _lesson, attendance = _make_absence(
        with_guardian=False,
        days_ago=120,
    )
    manager = User.objects.create_superuser(
        username="medical-registration-manager",
        password="test",
    )
    client.force_login(manager)

    registration = client.get(reverse("attendance_manager:medical_register"))
    assert registration.status_code == 200
    body = registration.content.decode()
    assert student.display_name in body
    assert "Зарегистрировать основание" in body

    response = client.post(
        reverse(
            "attendance_manager:medical_register_absence",
            kwargs={"attendance_id": attendance.id},
        ),
        follow=True,
    )

    assert response.status_code == 200
    justification = AbsenceJustification.objects.get(
        student=student,
        lesson=attendance.lesson,
        type=AbsenceJustification.Type.MEDICAL,
    )
    assert justification.status == AbsenceJustification.Status.PENDING
    assert justification.declared_by_id == manager.id
    assert AuditEvent.objects.filter(
        event_type="AbsenceJustificationDeclared",
        aggregate_id=justification.id,
        actor=manager,
    ).exists()
    assert "Медицинское основание зарегистрировано" in response.content.decode()


@pytest.mark.django_db
def test_manager_registration_service_requires_add_permission():
    _guardian, _student, _lesson, attendance = _make_absence(
        with_guardian=False,
        days_ago=10,
    )
    outsider = User.objects.create_user(
        username="medical-registration-outsider",
        password="test",
    )

    with pytest.raises(PermissionDenied):
        declare_medical_absence_by_manager(
            attendance_id=attendance.id,
            actor=outsider,
        )

    assert not AbsenceJustification.objects.filter(
        student=attendance.student,
        lesson=attendance.lesson,
    ).exists()
