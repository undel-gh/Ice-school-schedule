from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from django.contrib.auth import get_user_model

from accounts.models import CoachProfile
from core.testing import school_dt
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import generate_lessons


User = get_user_model()


@pytest.mark.django_db
def test_generate_lessons_reports_combined_coach_and_venue_conflict():
    actor = User.objects.create_superuser(
        username="combined-resource-manager",
        password="test-password",
    )
    coach_user = User.objects.create_user(username="combined-resource-coach")
    coach = CoachProfile.objects.create(
        user=coach_user,
        display_name="Combined Coach",
    )
    group_a = TrainingGroup.objects.create(code="combined-a", name="Group A")
    group_b = TrainingGroup.objects.create(code="combined-b", name="Group B")
    venue = Venue.objects.create(code="combined-rink", name="Rink")
    lesson_type = LessonType.objects.create(
        code="combined-ice",
        name="Ice",
        subscription_category="ice",
    )
    occurrence_date = date(2099, 1, 5)
    starts_at = school_dt(2099, 1, 5, 10, 0)
    template = ScheduleTemplate.objects.create(
        group=group_a,
        coach=coach,
        venue=venue,
        lesson_type=lesson_type,
        weekday=occurrence_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        valid_from=occurrence_date,
        valid_until=occurrence_date,
        is_active=True,
    )
    conflict = Lesson.objects.create(
        group=group_b,
        coach=coach,
        venue=venue,
        lesson_type=lesson_type,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )

    result = generate_lessons(
        template_id=template.id,
        from_date=occurrence_date,
        until_date=occurrence_date,
        actor=actor,
    )

    assert result.lessons == ()
    assert len(result.conflicts) == 1
    assert result.conflicts[0].conflicting_lesson_id == conflict.id
    assert result.conflicts[0].conflict_resource == "coach_and_venue"
    assert not Lesson.objects.filter(source_template=template).exists()
