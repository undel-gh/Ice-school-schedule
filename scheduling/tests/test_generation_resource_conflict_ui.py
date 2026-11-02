from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile
from core.time import make_school_aware, school_date, school_timezone
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import generate_lessons


User = get_user_model()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("same_coach", "same_venue", "expected_text"),
    [
        (True, False, "тот же тренер уже занят другим занятием"),
        (False, True, "та же площадка уже занята другим занятием"),
        (
            True,
            True,
            "тот же тренер и та же площадка уже заняты другим занятием",
        ),
    ],
)
def test_manager_generation_conflict_explains_busy_resource(
    client,
    same_coach,
    same_venue,
    expected_text,
):
    manager = User.objects.create_superuser(
        username=f"generation-conflict-ui-{same_coach}-{same_venue}",
        password="test",
    )
    coach_a = CoachProfile.objects.create(
        user=User.objects.create_user(
            username=f"generation-ui-coach-a-{same_coach}-{same_venue}",
            password="test",
        ),
        display_name="Coach A",
    )
    coach_b = CoachProfile.objects.create(
        user=User.objects.create_user(
            username=f"generation-ui-coach-b-{same_coach}-{same_venue}",
            password="test",
        ),
        display_name="Coach B",
    )
    group_a = TrainingGroup.objects.create(
        code=f"generation-ui-group-a-{same_coach}-{same_venue}",
        name="Group A",
    )
    group_b = TrainingGroup.objects.create(
        code=f"generation-ui-group-b-{same_coach}-{same_venue}",
        name="Group B",
    )
    venue_a = Venue.objects.create(
        code=f"generation-ui-venue-a-{same_coach}-{same_venue}",
        name="Venue A",
    )
    venue_b = Venue.objects.create(
        code=f"generation-ui-venue-b-{same_coach}-{same_venue}",
        name="Venue B",
    )
    lesson_type = LessonType.objects.create(
        code=f"generation-ui-ice-{same_coach}-{same_venue}",
        name="ICE",
        subscription_category="ice",
    )

    target_date = school_date(timezone.now()) + timedelta(days=30)
    starts_at = make_school_aware(datetime.combine(target_date, time(18, 0)))
    template = ScheduleTemplate.objects.create(
        group=group_a,
        lesson_type=lesson_type,
        coach=coach_a,
        venue=venue_a,
        weekday=target_date.weekday(),
        start_time=starts_at.astimezone(school_timezone()).time().replace(tzinfo=None),
        duration_minutes=60,
        valid_from=target_date,
        valid_until=target_date,
        is_active=True,
    )
    conflicting_coach = coach_a if same_coach else coach_b
    conflicting_venue = venue_a if same_venue else venue_b
    conflict = Lesson.objects.create(
        group=group_b,
        lesson_type=lesson_type,
        coach=conflicting_coach,
        venue=conflicting_venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )

    result = generate_lessons(
        template_id=template.id,
        from_date=target_date,
        until_date=target_date,
        actor=manager,
    )
    assert result.lessons == ()
    assert result.conflicts[0].conflicting_lesson_id == conflict.id

    client.force_login(manager)
    response = client.get(reverse("scheduling_manager:conflicts"))

    assert response.status_code == 200
    body = response.content.decode()
    assert expected_text in body
