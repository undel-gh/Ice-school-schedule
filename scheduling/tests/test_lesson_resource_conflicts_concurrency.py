from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.utils import timezone

from accounts.models import CoachProfile
from core.time import make_school_aware, school_date, school_timezone
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import generate_lessons, reschedule_lesson


User = get_user_model()


def _future_slot(*, days: int, hour: int = 18) -> datetime:
    on_date = school_date(timezone.now()) + timedelta(days=days)
    return make_school_aware(datetime.combine(on_date, time(hour, 0)))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shared_resource", ["coach", "venue"])
def test_concurrent_reschedule_and_generation_serialize_shared_resource(
    shared_resource,
):
    if connection.vendor != "postgresql":
        pytest.skip("Scheduling resource lock test requires PostgreSQL.")

    manager = User.objects.create_superuser(
        username=f"resource-race-manager-{shared_resource}",
        password="test",
    )
    coach_a = CoachProfile.objects.create(
        user=User.objects.create_user(
            username=f"resource-race-coach-a-{shared_resource}",
            password="test",
        ),
        display_name="Coach A",
    )
    coach_b = CoachProfile.objects.create(
        user=User.objects.create_user(
            username=f"resource-race-coach-b-{shared_resource}",
            password="test",
        ),
        display_name="Coach B",
    )
    group_a = TrainingGroup.objects.create(
        code=f"resource-race-group-a-{shared_resource}",
        name="Group A",
    )
    group_b = TrainingGroup.objects.create(
        code=f"resource-race-group-b-{shared_resource}",
        name="Group B",
    )
    venue_a = Venue.objects.create(
        code=f"resource-race-venue-a-{shared_resource}",
        name="Venue A",
    )
    venue_b = Venue.objects.create(
        code=f"resource-race-venue-b-{shared_resource}",
        name="Venue B",
    )
    lesson_type = LessonType.objects.create(
        code=f"resource-race-ice-{shared_resource}",
        name="ICE",
        subscription_category="ice",
    )

    source_start = _future_slot(days=20)
    target_start = _future_slot(days=30)
    source = Lesson.objects.create(
        group=group_a,
        lesson_type=lesson_type,
        coach=coach_a,
        venue=venue_a,
        starts_at=source_start,
        ends_at=source_start + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )

    template_coach = coach_a if shared_resource == "coach" else coach_b
    template_venue = venue_a if shared_resource == "venue" else venue_b
    target_date = school_date(target_start)
    template = ScheduleTemplate.objects.create(
        group=group_b,
        lesson_type=lesson_type,
        coach=template_coach,
        venue=template_venue,
        weekday=target_date.weekday(),
        start_time=(
            target_start.astimezone(school_timezone()).time().replace(tzinfo=None)
        ),
        duration_minutes=60,
        valid_from=target_date,
        valid_until=target_date,
        is_active=True,
    )

    barrier = threading.Barrier(2)

    def reschedule_worker():
        close_old_connections()
        try:
            actor = User.objects.get(pk=manager.pk)
            barrier.wait(timeout=10)
            try:
                reschedule_lesson(
                    lesson_id=source.id,
                    new_starts_at=target_start,
                    new_ends_at=target_start + timedelta(hours=1),
                    actor=actor,
                    reason=Lesson.CancellationReason.ADMINISTRATIVE,
                    now=timezone.now(),
                )
            except ValidationError:
                return "reschedule-blocked"
            return "reschedule-created"
        finally:
            connections.close_all()

    def generation_worker():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            result = generate_lessons(
                template_id=template.id,
                from_date=target_date,
                until_date=target_date,
                actor=None,
            )
            if result.lessons:
                return "generation-created"
            assert len(result.conflicts) == 1
            return "generation-blocked"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(reschedule_worker),
            pool.submit(generation_worker),
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sum(result.endswith("created") for result in results) == 1
    assert sum(result.endswith("blocked") for result in results) == 1

    target_lessons = Lesson.objects.filter(
        starts_at__lt=target_start + timedelta(hours=1),
        ends_at__gt=target_start,
    ).exclude(status=Lesson.Status.CANCELLED)
    if shared_resource == "coach":
        target_lessons = target_lessons.filter(coach=coach_a)
    else:
        target_lessons = target_lessons.filter(venue=venue_a)
    assert target_lessons.count() == 1
