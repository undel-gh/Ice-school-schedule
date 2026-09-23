from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import CoachProfile
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue


@pytest.fixture
def ops_context(db, django_user_model):
    actor = django_user_model.objects.create_user(
        username="schedule-ops",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    coach_user = django_user_model.objects.create_user(
        username="schedule-coach",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Coach",
    )
    group = TrainingGroup.objects.create(
        code="ops-group",
        name="Ops Group",
    )
    venue = Venue.objects.create(
        code="ops-rink",
        name="Ops Rink",
    )
    lesson_type = LessonType.objects.create(
        code="ops-ice",
        name="Ice",
        subscription_category="ice",
    )
    return actor, coach, group, venue, lesson_type


def make_lesson(*, ops_context, status, starts_at):
    actor, coach, group, venue, lesson_type = ops_context
    return Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=status,
    )


@pytest.mark.django_db
def test_confirm_lesson_command(ops_context):
    actor, *_ = ops_context
    starts_at = datetime(
        2099,
        10,
        1,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        ops_context=ops_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at,
    )

    out = StringIO()
    call_command(
        "confirm_lesson",
        "--lesson",
        str(lesson.id),
        "--actor",
        actor.username,
        stdout=out,
    )

    lesson.refresh_from_db()
    assert lesson.status == Lesson.Status.CONFIRMED
    assert "confirmed" in out.getvalue().lower()


@pytest.mark.django_db
def test_reschedule_lesson_command_uses_cross_app_workflow(ops_context):
    actor, *_ = ops_context
    starts_at = datetime(
        2099,
        10,
        1,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    source = make_lesson(
        ops_context=ops_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at,
    )

    out = StringIO()
    call_command(
        "reschedule_lesson",
        "--lesson",
        str(source.id),
        "--starts-at",
        "2099-10-02T15:00:00+00:00",
        "--ends-at",
        "2099-10-02T16:00:00+00:00",
        "--reason",
        Lesson.CancellationReason.ADMINISTRATIVE,
        "--actor",
        actor.username,
        stdout=out,
    )

    source.refresh_from_db()
    assert source.status == Lesson.Status.CANCELLED
    assert source.replacement_lesson_id is not None
    assert "Replacement lesson" in out.getvalue()
