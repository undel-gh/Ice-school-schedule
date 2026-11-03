from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.db.models.query import QuerySet
from django.utils import timezone

from accounts.models import CoachProfile
from core.time import make_school_aware, school_date
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from scheduling.services import publish_lesson, reschedule_lesson


User = get_user_model()


@pytest.mark.django_db(transaction=True)
def test_concurrent_publish_and_reschedule_use_compatible_lock_order(monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Scheduling lock-order test requires PostgreSQL.")

    manager = User.objects.create_superuser(
        username="publish-reschedule-lock-manager",
        password="test",
    )
    coach = CoachProfile.objects.create(
        user=User.objects.create_user(
            username="publish-reschedule-lock-coach",
            password="test",
        ),
        display_name="Coach",
    )
    group = TrainingGroup.objects.create(
        code="publish-reschedule-lock-group",
        name="Group",
    )
    venue = Venue.objects.create(
        code="publish-reschedule-lock-venue",
        name="Venue",
    )
    lesson_type = LessonType.objects.create(
        code="publish-reschedule-lock-ice",
        name="ICE",
        subscription_category="ice",
    )

    source_date = school_date(timezone.now()) + timedelta(days=30)
    source_start = make_school_aware(datetime.combine(source_date, time(18, 0)))
    target_start = source_start + timedelta(days=1)
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=source_start,
        ends_at=source_start + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )

    publish_lesson_locked = threading.Event()
    reschedule_venue_locked = threading.Event()
    worker_role = threading.local()
    original_get = QuerySet.get

    def coordinated_get(self, *args, **kwargs):
        obj = original_get(self, *args, **kwargs)
        if not self.query.select_for_update:
            return obj

        role = getattr(worker_role, "value", None)
        if role == "publish" and self.model is Lesson and obj.pk == source.pk:
            publish_lesson_locked.set()
            # With the safe Group -> Coach -> Lesson -> Venue order, reschedule
            # blocks on Lesson and cannot reach Venue until publish commits.
            # With the old Venue -> Lesson order it acquires Venue here, which
            # lets publish form the opposite Lesson -> Venue edge and deadlock.
            reschedule_venue_locked.wait(timeout=1)
        elif role == "reschedule" and self.model is Venue and obj.pk == venue.pk:
            reschedule_venue_locked.set()
        return obj

    monkeypatch.setattr(QuerySet, "get", coordinated_get)

    def publish_worker():
        close_old_connections()
        worker_role.value = "publish"
        try:
            actor = User.objects.get(pk=manager.pk)
            try:
                publish_lesson(
                    lesson_id=source.id,
                    actor=actor,
                    now=timezone.now(),
                )
            except ValidationError:
                return "publish-blocked"
            return "published"
        finally:
            connections.close_all()

    def reschedule_worker():
        close_old_connections()
        worker_role.value = "reschedule"
        try:
            assert publish_lesson_locked.wait(timeout=5)
            actor = User.objects.get(pk=manager.pk)
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
            return "rescheduled"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        publish_future = pool.submit(publish_worker)
        reschedule_future = pool.submit(reschedule_worker)
        results = {
            publish_future.result(timeout=15),
            reschedule_future.result(timeout=15),
        }

    assert results == {"published", "rescheduled"}
    source.refresh_from_db()
    assert source.status == Lesson.Status.CANCELLED
    assert source.replacement_lesson_id is not None
