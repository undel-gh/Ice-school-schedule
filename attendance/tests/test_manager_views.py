from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile, Student
from attendance.models import AbsenceJustification, Attendance
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue

User = get_user_model()


@pytest.mark.django_db
def test_manager_verifies_pending_medical_absence(client):
    manager = User.objects.create_user(
        username="medical-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = User.objects.create_user(username="medical-coach", password="test")
    coach = CoachProfile.objects.create(user=coach_user, display_name="Coach")
    group = TrainingGroup.objects.create(code="medical-group", name="Medical Group")
    venue = Venue.objects.create(code="medical-venue", name="Medical Venue")
    lesson_type = LessonType.objects.create(
        code="medical-ice",
        name="ICE",
        subscription_category="ice",
    )
    student = Student.objects.create(display_name="Medical Student")
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
    Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.ABSENT,
        marked_at=lesson.ends_at,
        marked_by=manager,
        updated_by=manager,
    )
    justification = AbsenceJustification.objects.create(
        student=student,
        lesson=lesson,
        type=AbsenceJustification.Type.MEDICAL,
        status=AbsenceJustification.Status.PENDING,
        declared_by=manager,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "attendance_manager:medical_verify",
            kwargs={"justification_id": justification.id},
        ),
        {"valid_until": (timezone.localdate() + timedelta(days=60)).isoformat()},
    )

    assert response.status_code == 302
    justification.refresh_from_db()
    assert justification.status == AbsenceJustification.Status.VERIFIED
    assert justification.reviewed_by == manager


@pytest.mark.django_db
def test_manager_post_checks_permission_before_medical_lookup(client):
    import uuid

    outsider = User.objects.create_user(
        username="medical-outsider",
        password="test",
    )
    client.force_login(outsider)

    response = client.post(
        reverse(
            "attendance_manager:medical_reject",
            kwargs={"justification_id": uuid.uuid4()},
        )
    )

    assert response.status_code == 403
