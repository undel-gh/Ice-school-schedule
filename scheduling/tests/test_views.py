from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import CoachProfile, Student, StudentAccess
from attendance.models import Attendance
from scheduling.models import (
    Lesson,
    LessonResponse,
    LessonRosterEntry,
    LessonType,
    TrainingGroup,
    Venue,
)

User = get_user_model()


@pytest.fixture
def web_context(db):
    coach_user = User.objects.create_user(
        username="web-coach",
        password="test",
    )
    other_coach_user = User.objects.create_user(
        username="other-coach",
        password="test",
    )
    guardian = User.objects.create_user(
        username="web-guardian",
        password="test",
    )

    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Coach",
    )
    other_coach = CoachProfile.objects.create(
        user=other_coach_user,
        display_name="Other coach",
    )
    group = TrainingGroup.objects.create(
        code="web-group",
        name="Web Group",
    )
    venue = Venue.objects.create(
        code="web-rink",
        name="Web Rink",
    )
    lesson_type = LessonType.objects.create(
        code="web-ice",
        name="Ice",
        subscription_category="ice",
    )
    student = Student.objects.create(display_name="Student A")
    other_student = Student.objects.create(display_name="Student B")
    StudentAccess.objects.create(
        user=guardian,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )

    return {
        "coach_user": coach_user,
        "other_coach_user": other_coach_user,
        "guardian": guardian,
        "coach": coach,
        "other_coach": other_coach,
        "group": group,
        "venue": venue,
        "lesson_type": lesson_type,
        "student": student,
        "other_student": other_student,
    }


def make_lesson(
    *,
    context,
    coach=None,
    status=Lesson.Status.RSVP_OPEN,
    starts_at=None,
):
    starts_at = starts_at or datetime(
        2026,
        9,
        25,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    return Lesson.objects.create(
        group=context["group"],
        lesson_type=context["lesson_type"],
        coach=coach or context["coach"],
        venue=context["venue"],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=status,
    )


@pytest.mark.django_db
def test_guardian_sees_only_accessible_student_schedule(client, web_context):
    lesson = make_lesson(context=web_context)
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=web_context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=web_context["coach_user"],
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=web_context["other_student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=web_context["coach_user"],
    )

    client.force_login(web_context["guardian"])
    response = client.get(
        reverse("scheduling:student_schedule"),
        {
            "from": "2026-09-25",
            "until": "2026-09-25",
        },
    )

    assert response.status_code == 200
    assert "Student A" in response.content.decode()
    assert "Student B" not in response.content.decode()


@pytest.mark.django_db
def test_guardian_cannot_select_inaccessible_student(client, web_context):
    client.force_login(web_context["guardian"])

    response = client.get(
        reverse("scheduling:student_schedule"),
        {"student": str(web_context["other_student"].id)},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_guardian_rsvp_post_uses_service(client, web_context):
    lesson = make_lesson(context=web_context)
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=web_context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=web_context["coach_user"],
    )

    client.force_login(web_context["guardian"])
    response = client.post(
        reverse(
            "scheduling:set_rsvp",
            kwargs={
                "student_id": web_context["student"].id,
                "lesson_id": lesson.id,
            },
        ),
        {"status": LessonResponse.Status.YES},
    )

    assert response.status_code == 302
    assert LessonResponse.objects.get(
        lesson=lesson,
        student=web_context["student"],
    ).status == LessonResponse.Status.YES


@pytest.mark.django_db
def test_coach_sees_own_schedule(client, web_context):
    own = make_lesson(context=web_context)
    other = make_lesson(
        context=web_context,
        coach=web_context["other_coach"],
        starts_at=own.starts_at + timedelta(hours=2),
    )

    client.force_login(web_context["coach_user"])
    response = client.get(
        reverse("scheduling:coach_schedule"),
        {"date": "2026-09-25"},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert str(own.starts_at.astimezone(dt_timezone.utc).hour) or body
    assert reverse(
        "scheduling:coach_lesson",
        kwargs={"lesson_id": own.id},
    ) in body
    assert reverse(
        "scheduling:coach_lesson",
        kwargs={"lesson_id": other.id},
    ) not in body


@pytest.mark.django_db
def test_coach_cannot_open_other_coach_lesson(client, web_context):
    lesson = make_lesson(
        context=web_context,
        coach=web_context["other_coach"],
    )

    client.force_login(web_context["coach_user"])
    response = client.get(
        reverse(
            "scheduling:coach_lesson",
            kwargs={"lesson_id": lesson.id},
        )
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_coach_attendance_post_uses_service(client, web_context):
    lesson = make_lesson(
        context=web_context,
        status=Lesson.Status.CONFIRMED,
        starts_at=datetime(
            2026,
            9,
            20,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=web_context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=web_context["coach_user"],
    )

    client.force_login(web_context["coach_user"])
    response = client.post(
        reverse(
            "scheduling:coach_set_attendance",
            kwargs={
                "lesson_id": lesson.id,
                "student_id": web_context["student"].id,
            },
        ),
        {"status": Attendance.Status.PRESENT},
    )

    assert response.status_code == 302
    assert Attendance.objects.get(
        lesson=lesson,
        student=web_context["student"],
    ).status == Attendance.Status.PRESENT



@pytest.mark.django_db
def test_student_schedule_renders_mobile_touch_controls(client, web_context):
    lesson = make_lesson(context=web_context)
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=web_context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=web_context["coach_user"],
    )

    client.force_login(web_context["guardian"])
    response = client.get(
        reverse("scheduling:student_schedule"),
        {
            "from": "2026-09-25",
            "until": "2026-09-25",
        },
    )

    body = response.content.decode()
    assert 'class="lesson-card"' in body
    assert "✓ Буду" in body
    assert "Не буду" in body


@pytest.mark.django_db
def test_coach_lesson_renders_mobile_cards_and_bulk_actions(
    client,
    web_context,
):
    lesson = make_lesson(
        context=web_context,
        status=Lesson.Status.CONFIRMED,
        starts_at=datetime(
            2026,
            9,
            20,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=web_context["student"],
        source=LessonRosterEntry.Source.MANUAL,
        added_by=web_context["coach_user"],
    )

    client.force_login(web_context["coach_user"])
    response = client.get(
        reverse(
            "scheduling:coach_lesson",
            kwargs={"lesson_id": lesson.id},
        )
    )

    body = response.content.decode()
    assert 'class="student-card"' in body
    assert "<table" not in body
    assert 'class="bulk-actions"' in body
    assert "✓ Пришёл" in body
    assert "Оставшиеся отсутствуют" in body



@pytest.mark.django_db
def test_rsvp_unknown_lesson_returns_404(client, web_context):
    import uuid

    client.force_login(web_context["guardian"])
    response = client.post(
        reverse(
            "scheduling:set_rsvp",
            kwargs={
                "student_id": web_context["student"].id,
                "lesson_id": uuid.uuid4(),
            },
        ),
        {"status": LessonResponse.Status.YES},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_student_schedule_rejects_excessive_date_range(client, web_context):
    client.force_login(web_context["guardian"])
    response = client.get(
        reverse("scheduling:student_schedule"),
        {
            "from": "2026-01-01",
            "until": "2027-12-31",
        },
    )

    assert response.status_code == 404
