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
from core.time import make_school_aware, school_date, school_timezone
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import (
    publish_lesson,
    reschedule_lesson,
    version_schedule_template,
)


User = get_user_model()


def _setup_context(prefix: str):
    manager = User.objects.create_superuser(
        username=f"{prefix}-manager",
        password="test",
    )
    coach_a = CoachProfile.objects.create(
        user=User.objects.create_user(
            username=f"{prefix}-coach-a",
            password="test",
        ),
        display_name="Coach A",
    )
    coach_b = CoachProfile.objects.create(
        user=User.objects.create_user(
            username=f"{prefix}-coach-b",
            password="test",
        ),
        display_name="Coach B",
    )
    group_a = TrainingGroup.objects.create(
        code=f"{prefix}-group-a",
        name="Group A",
    )
    group_b = TrainingGroup.objects.create(
        code=f"{prefix}-group-b",
        name="Group B",
    )
    venue_a = Venue.objects.create(
        code=f"{prefix}-venue-a",
        name="Venue A",
    )
    venue_b = Venue.objects.create(
        code=f"{prefix}-venue-b",
        name="Venue B",
    )
    lesson_type_a = LessonType.objects.create(
        code=f"{prefix}-ice-a",
        name="ICE A",
        subscription_category="ice",
    )
    lesson_type_b = LessonType.objects.create(
        code=f"{prefix}-ice-b",
        name="ICE B",
        subscription_category="ice",
    )

    today = school_date(timezone.now())
    source_date = today + timedelta(days=30)
    source_start = make_school_aware(datetime.combine(source_date, time(18, 0)))
    template = ScheduleTemplate.objects.create(
        group=group_a,
        lesson_type=lesson_type_a,
        coach=coach_a,
        venue=venue_a,
        weekday=source_date.weekday(),
        start_time=(
            source_start.astimezone(school_timezone()).time().replace(tzinfo=None)
        ),
        duration_minutes=60,
        valid_from=today,
        is_active=True,
    )
    source = Lesson.objects.create(
        source_template=template,
        group=group_a,
        lesson_type=lesson_type_a,
        coach=coach_a,
        venue=venue_a,
        starts_at=source_start,
        ends_at=source_start + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=source_start - timedelta(hours=2),
        decision_deadline=source_start - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    return {
        "manager": manager,
        "coach_b": coach_b,
        "group_b": group_b,
        "venue_a": venue_a,
        "venue_b": venue_b,
        "lesson_type_a": lesson_type_a,
        "lesson_type_b": lesson_type_b,
        "template": template,
        "source": source,
        "source_start": source_start,
        "effective_from": today + timedelta(days=20),
    }


@pytest.mark.django_db(transaction=True)
def test_concurrent_versioning_and_reschedule_use_compatible_lock_order(monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Scheduling lock-order test requires PostgreSQL.")

    ctx = _setup_context("version-reschedule-lock")
    source = ctx["source"]
    target_start = ctx["source_start"] + timedelta(days=1)
    reschedule_lesson_locked = threading.Event()
    version_venue_locked = threading.Event()
    worker_role = threading.local()
    original_get = QuerySet.get

    def coordinated_get(self, *args, **kwargs):
        obj = original_get(self, *args, **kwargs)
        if not self.query.select_for_update:
            return obj

        role = getattr(worker_role, "value", None)
        if role == "reschedule" and self.model is Lesson and obj.pk == source.pk:
            reschedule_lesson_locked.set()
            # Safe versioning blocks on the Lesson before reaching Venue.
            # The old Venue -> Lesson order sets this event and then waits for
            # the Lesson, while reschedule proceeds to Venue: a deadlock cycle.
            version_venue_locked.wait(timeout=1)
        elif (
            role == "version"
            and self.model is Venue
            and obj.pk == ctx["venue_a"].pk
        ):
            version_venue_locked.set()
        return obj

    monkeypatch.setattr(QuerySet, "get", coordinated_get)

    def reschedule_worker():
        close_old_connections()
        worker_role.value = "reschedule"
        try:
            actor = User.objects.get(pk=ctx["manager"].pk)
            return reschedule_lesson(
                lesson_id=source.id,
                new_starts_at=target_start,
                new_ends_at=target_start + timedelta(hours=1),
                actor=actor,
                reason=Lesson.CancellationReason.ADMINISTRATIVE,
                now=timezone.now(),
            )
        finally:
            connections.close_all()

    def version_worker():
        close_old_connections()
        worker_role.value = "version"
        try:
            assert reschedule_lesson_locked.wait(timeout=5)
            actor = User.objects.get(pk=ctx["manager"].pk)
            return version_schedule_template(
                template_id=ctx["template"].id,
                effective_from=ctx["effective_from"],
                actor=actor,
                now=timezone.now(),
                group_id=ctx["group_b"].id,
                coach_id=ctx["coach_b"].id,
                lesson_type_id=ctx["lesson_type_b"].id,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reschedule_future = pool.submit(reschedule_worker)
        version_future = pool.submit(version_worker)
        replacement = reschedule_future.result(timeout=15)
        replacement_template = version_future.result(timeout=15)

    assert replacement.pk is not None
    assert replacement_template.pk is not None
    source.refresh_from_db()
    assert source.status == Lesson.Status.CANCELLED
    assert source.replacement_lesson_id == replacement.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shared_resource", ["lesson_type", "venue"])
def test_concurrent_publish_and_versioning_use_compatible_lock_order(
    monkeypatch,
    shared_resource,
):
    if connection.vendor != "postgresql":
        pytest.skip("Scheduling lock-order test requires PostgreSQL.")

    ctx = _setup_context(f"publish-version-lock-{shared_resource}")
    source = ctx["source"]
    publish_lesson_locked = threading.Event()
    version_resource_locked = threading.Event()
    worker_role = threading.local()
    original_get = QuerySet.get

    if shared_resource == "lesson_type":
        target_lesson_type_id = ctx["lesson_type_a"].id
        target_venue_id = ctx["venue_b"].id
        watched_model = LessonType
        watched_pk = ctx["lesson_type_a"].pk
    else:
        target_lesson_type_id = ctx["lesson_type_b"].id
        target_venue_id = ctx["venue_a"].id
        watched_model = Venue
        watched_pk = ctx["venue_a"].pk

    def coordinated_get(self, *args, **kwargs):
        obj = original_get(self, *args, **kwargs)
        if not self.query.select_for_update:
            return obj

        role = getattr(worker_role, "value", None)
        if role == "publish" and self.model is Lesson and obj.pk == source.pk:
            publish_lesson_locked.set()
            # Safe versioning blocks on the Lesson before LessonType/Venue.
            # The old resource -> Lesson order lets versioning hold the shared
            # resource while it waits for this Lesson, completing a cycle when
            # publication continues to that resource.
            version_resource_locked.wait(timeout=1)
        elif (
            role == "version"
            and self.model is watched_model
            and obj.pk == watched_pk
        ):
            version_resource_locked.set()
        return obj

    monkeypatch.setattr(QuerySet, "get", coordinated_get)

    def publish_worker():
        close_old_connections()
        worker_role.value = "publish"
        try:
            actor = User.objects.get(pk=ctx["manager"].pk)
            return publish_lesson(
                lesson_id=source.id,
                actor=actor,
                now=timezone.now(),
            )
        finally:
            connections.close_all()

    def version_worker():
        close_old_connections()
        worker_role.value = "version"
        try:
            assert publish_lesson_locked.wait(timeout=5)
            actor = User.objects.get(pk=ctx["manager"].pk)
            try:
                version_schedule_template(
                    template_id=ctx["template"].id,
                    effective_from=ctx["effective_from"],
                    actor=actor,
                    now=timezone.now(),
                    group_id=ctx["group_b"].id,
                    coach_id=ctx["coach_b"].id,
                    lesson_type_id=target_lesson_type_id,
                    venue_id=target_venue_id,
                )
            except ValidationError:
                return "version-blocked"
            return "versioned"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        publish_future = pool.submit(publish_worker)
        version_future = pool.submit(version_worker)
        published = publish_future.result(timeout=15)
        version_result = version_future.result(timeout=15)

    assert published.status == Lesson.Status.RSVP_OPEN
    assert version_result == "version-blocked"
    source.refresh_from_db()
    assert source.status == Lesson.Status.RSVP_OPEN
