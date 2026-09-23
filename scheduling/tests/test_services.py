from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.models import CoachProfile
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from scheduling.services import complete_lesson

User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(username="coach-lifecycle", password="test")


@pytest.fixture
def school_context(db, coach_user):
    coach = CoachProfile.objects.create(user=coach_user, display_name="Coach")
    group = TrainingGroup.objects.create(code="g-life", name="Group")
    venue = Venue.objects.create(code="v-life", name="Venue")
    lesson_type = LessonType.objects.create(
        code="ice-life",
        name="Ice",
        subscription_category="ice",
    )
    return coach, group, venue, lesson_type


def make_lesson(*, school_context, status, starts_at):
    coach, group, venue, lesson_type = school_context
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
def test_complete_lesson_transitions_confirmed_to_completed(school_context):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.CONFIRMED,
        starts_at=starts_at,
    )
    now = starts_at + timedelta(hours=1, minutes=1)

    completed = complete_lesson(lesson_id=lesson.id, now=now)

    assert completed.status == Lesson.Status.COMPLETED
    assert completed.completed_at == now


@pytest.mark.django_db
def test_complete_lesson_rejects_early_completion(school_context):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.CONFIRMED,
        starts_at=starts_at,
    )

    with pytest.raises(ValidationError):
        complete_lesson(
            lesson_id=lesson.id,
            now=starts_at + timedelta(minutes=30),
        )


@pytest.mark.django_db
def test_complete_lesson_rejects_wrong_state(school_context):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at,
    )

    with pytest.raises(ValidationError):
        complete_lesson(
            lesson_id=lesson.id,
            now=starts_at + timedelta(hours=2),
        )
