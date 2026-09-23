from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import CoachProfile, Student
from attendance.models import AbsenceJustification, Attendance
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue


@pytest.mark.django_db
def test_verify_medical_absence_command(django_user_model):
    actor = django_user_model.objects.create_user(
        username="medical-ops",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = django_user_model.objects.create_user(
        username="medical-coach",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Coach",
    )
    student = Student.objects.create(display_name="Student")
    group = TrainingGroup.objects.create(
        code="medical-group",
        name="Medical Group",
    )
    venue = Venue.objects.create(
        code="medical-rink",
        name="Medical Rink",
    )
    lesson_type = LessonType.objects.create(
        code="medical-ice",
        name="Ice",
        subscription_category="ice",
    )
    starts_at = datetime(
        2099,
        10,
        1,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
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
        marked_by=coach_user,
    )
    justification = AbsenceJustification.objects.create(
        student=student,
        lesson=lesson,
        type=AbsenceJustification.Type.MEDICAL,
        status=AbsenceJustification.Status.PENDING,
        verification_method=AbsenceJustification.VerificationMethod.IN_PERSON,
        declared_by=actor,
    )

    out = StringIO()
    call_command(
        "verify_medical_absence",
        "--justification",
        str(justification.id),
        "--valid-until",
        "2099-10-31",
        "--actor",
        actor.username,
        stdout=out,
    )

    justification.refresh_from_db()
    assert justification.status == AbsenceJustification.Status.VERIFIED
    assert "verified" in out.getvalue().lower()
