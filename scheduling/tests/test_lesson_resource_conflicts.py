from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CoachProfile
from audit.models import AuditEvent
from core.time import make_school_aware, school_date, school_timezone
from ice_school.workflows import reschedule_lesson_with_entitlements
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import generate_lessons, reschedule_lesson


User = get_user_model()


@pytest.fixture
def resource_context(db):
    manager = User.objects.create_superuser(
        username="resource-conflict-manager",
        password="test",
    )
    coach_a_user = User.objects.create_user(
        username="resource-coach-a",
        password="test",
    )
    coach_b_user = User.objects.create_user(
        username="resource-coach-b",
        password="test",
    )
    return {
        "manager": manager,
        "coach_a": CoachProfile.objects.create(
            user=coach_a_user,
            display_name="Coach A",
        ),
        "coach_b": CoachProfile.objects.create(
            user=coach_b_user,
            display_name="Coach B",
        ),
        "group_a": TrainingGroup.objects.create(
            code="resource-group-a",
            name="Resource Group A",
        ),
        "group_b": TrainingGroup.objects.create(
            code="resource-group-b",
            name="Resource Group B",
        ),
        "venue_a": Venue.objects.create(
            code="resource-venue-a",
            name="Resource Venue A",
        ),
        "venue_b": Venue.objects.create(
            code="resource-venue-b",
            name="Resource Venue B",
        ),
        "lesson_type": LessonType.objects.create(
            code="resource-ice",
            name="Resource ICE",
            subscription_category="ice",
        ),
    }


def _slot(*, days: int, hour: int = 18) -> datetime:
    on_date = school_date(timezone.now()) + timedelta(days=days)
    return make_school_aware(datetime.combine(on_date, time(hour, 0)))


def _lesson(
    *,
    ctx,
    starts_at: datetime,
    group_key: str = "group_a",
    coach_key: str = "coach_a",
    venue_key: str = "venue_a",
    status: str = Lesson.Status.DRAFT,
) -> Lesson:
    values = {
        "group": ctx[group_key],
        "lesson_type": ctx["lesson_type"],
        "coach": ctx[coach_key],
        "venue": ctx[venue_key],
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(hours=1),
        "minimum_attendees": 1,
        "rsvp_deadline": starts_at - timedelta(hours=2),
        "decision_deadline": starts_at - timedelta(hours=1),
        "status": status,
    }
    if status == Lesson.Status.CANCELLED:
        values.update(
            cancelled_at=starts_at - timedelta(days=1),
            cancellation_reason=Lesson.CancellationReason.ADMINISTRATIVE,
        )
    return Lesson.objects.create(**values)


def _source(ctx) -> Lesson:
    return _lesson(
        ctx=ctx,
        starts_at=_slot(days=20),
        status=Lesson.Status.RSVP_OPEN,
    )


def _reschedule(source: Lesson, *, starts_at: datetime, ctx):
    return reschedule_lesson(
        lesson_id=source.id,
        new_starts_at=starts_at,
        new_ends_at=starts_at + timedelta(hours=1),
        actor=ctx["manager"],
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=timezone.now(),
    )


@pytest.mark.django_db
def test_reschedule_preserves_same_group_conflict_guard(resource_context):
    ctx = resource_context
    source = _source(ctx)
    target = _slot(days=30)
    conflict = _lesson(ctx=ctx, starts_at=target)

    with pytest.raises(ValidationError, match="lesson of this group"):
        _reschedule(source, starts_at=target, ctx=ctx)

    source.refresh_from_db()
    assert source.status == Lesson.Status.RSVP_OPEN
    assert source.replacement_lesson_id is None
    assert Lesson.objects.filter(replaced_lesson=source).count() == 0
    assert conflict.status == Lesson.Status.DRAFT


@pytest.mark.django_db
def test_reschedule_rejects_same_coach_in_another_group(resource_context):
    ctx = resource_context
    source = _source(ctx)
    target = _slot(days=30)
    conflict = _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key="coach_a",
        venue_key="venue_b",
    )

    with pytest.raises(ValidationError, match="same coach"):
        _reschedule(source, starts_at=target, ctx=ctx)

    source.refresh_from_db()
    assert source.status == Lesson.Status.RSVP_OPEN
    assert source.replacement_lesson_id is None
    assert conflict.status == Lesson.Status.DRAFT


@pytest.mark.django_db
def test_reschedule_rejects_same_venue_for_another_group(resource_context):
    ctx = resource_context
    source = _source(ctx)
    target = _slot(days=30)
    conflict = _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key="coach_b",
        venue_key="venue_a",
    )

    with pytest.raises(ValidationError, match="same venue"):
        _reschedule(source, starts_at=target, ctx=ctx)

    source.refresh_from_db()
    assert source.status == Lesson.Status.RSVP_OPEN
    assert source.replacement_lesson_id is None
    assert conflict.status == Lesson.Status.DRAFT


@pytest.mark.django_db
def test_reschedule_ignores_cancelled_resource_conflicts(resource_context):
    ctx = resource_context
    source = _source(ctx)
    target = _slot(days=30)
    _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key="coach_a",
        venue_key="venue_b",
        status=Lesson.Status.CANCELLED,
    )
    _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key="coach_b",
        venue_key="venue_a",
        status=Lesson.Status.CANCELLED,
    )

    replacement = _reschedule(source, starts_at=target, ctx=ctx)

    assert replacement.starts_at == target
    assert replacement.coach_id == ctx["coach_a"].id
    assert replacement.venue_id == ctx["venue_a"].id
    source.refresh_from_db()
    assert source.status == Lesson.Status.CANCELLED
    assert source.replacement_lesson_id == replacement.id


@pytest.mark.django_db
def test_reschedule_allows_free_resource_slot(resource_context):
    ctx = resource_context
    source = _source(ctx)
    target = _slot(days=30)
    _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key="coach_b",
        venue_key="venue_b",
    )

    replacement = _reschedule(source, starts_at=target, ctx=ctx)

    assert replacement.starts_at == target
    source.refresh_from_db()
    assert source.replacement_lesson_id == replacement.id


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("coach_key", "venue_key", "expected"),
    [
        ("coach_a", "venue_b", "same coach"),
        ("coach_b", "venue_a", "same venue"),
    ],
)
def test_workflow_rejects_unmaterialized_template_resource_conflict(
    resource_context,
    coach_key,
    venue_key,
    expected,
):
    ctx = resource_context
    source = _source(ctx)
    target = _slot(days=35)
    target_date = school_date(target)
    ScheduleTemplate.objects.create(
        group=ctx["group_b"],
        lesson_type=ctx["lesson_type"],
        coach=ctx[coach_key],
        venue=ctx[venue_key],
        weekday=target_date.weekday(),
        start_time=target.astimezone(school_timezone()).time().replace(tzinfo=None),
        duration_minutes=60,
        valid_from=target_date,
        valid_until=target_date,
        is_active=True,
    )

    with pytest.raises(ValidationError, match=expected):
        reschedule_lesson_with_entitlements(
            lesson_id=source.id,
            new_starts_at=target,
            new_ends_at=target + timedelta(hours=1),
            actor=ctx["manager"],
            reason=Lesson.CancellationReason.ADMINISTRATIVE,
            now=timezone.now(),
        )

    source.refresh_from_db()
    assert source.status == Lesson.Status.RSVP_OPEN
    assert source.replacement_lesson_id is None


def _template_for_target(ctx, *, target, coach_key="coach_a", venue_key="venue_a"):
    target_date = school_date(target)
    return ScheduleTemplate.objects.create(
        group=ctx["group_a"],
        lesson_type=ctx["lesson_type"],
        coach=ctx[coach_key],
        venue=ctx[venue_key],
        weekday=target_date.weekday(),
        start_time=target.astimezone(school_timezone()).time().replace(tzinfo=None),
        duration_minutes=60,
        valid_from=target_date,
        valid_until=target_date,
        is_active=True,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("coach_key", "venue_key", "expected_resources"),
    [
        ("coach_a", "venue_b", ["coach"]),
        ("coach_b", "venue_a", ["venue"]),
        ("coach_a", "venue_a", ["coach", "venue"]),
    ],
)
def test_generation_reports_other_group_resource_conflict(
    resource_context,
    coach_key,
    venue_key,
    expected_resources,
):
    ctx = resource_context
    target = _slot(days=40)
    template = _template_for_target(ctx, target=target)
    conflict = _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key=coach_key,
        venue_key=venue_key,
    )

    result = generate_lessons(
        template_id=template.id,
        from_date=school_date(target),
        until_date=school_date(target),
        actor=ctx["manager"],
    )

    assert result.lessons == ()
    assert len(result.conflicts) == 1
    assert result.conflicts[0].conflicting_lesson_id == conflict.id
    event = AuditEvent.objects.get(
        event_type="LessonGenerationConflict",
        aggregate_id=template.id,
    )
    assert event.payload["conflict_resources"] == expected_resources
    assert not Lesson.objects.filter(source_template=template).exists()


@pytest.mark.django_db
def test_generation_ignores_cancelled_resource_overlap(resource_context):
    ctx = resource_context
    target = _slot(days=40)
    template = _template_for_target(ctx, target=target)
    _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key="coach_a",
        venue_key="venue_a",
        status=Lesson.Status.CANCELLED,
    )

    result = generate_lessons(
        template_id=template.id,
        from_date=school_date(target),
        until_date=school_date(target),
        actor=ctx["manager"],
    )

    assert len(result.lessons) == 1
    assert result.conflicts == ()
    assert result.lessons[0].source_template_id == template.id


@pytest.mark.django_db
def test_generation_preserves_same_group_same_type_existing_semantics(resource_context):
    ctx = resource_context
    target = _slot(days=40)
    template = _template_for_target(ctx, target=target)
    existing = _lesson(ctx=ctx, starts_at=target)

    result = generate_lessons(
        template_id=template.id,
        from_date=school_date(target),
        until_date=school_date(target),
        actor=ctx["manager"],
    )

    assert result.lessons == (existing,)
    assert result.conflicts == ()
    assert not Lesson.objects.filter(source_template=template).exists()


@pytest.mark.django_db
def test_manager_reschedule_keeps_source_when_coach_is_busy(
    client,
    resource_context,
):
    ctx = resource_context
    source = _source(ctx)
    target = _slot(days=30)
    _lesson(
        ctx=ctx,
        starts_at=target,
        group_key="group_b",
        coach_key="coach_a",
        venue_key="venue_b",
    )
    local_target = target.astimezone(school_timezone())
    local_end = (target + timedelta(hours=1)).astimezone(school_timezone())
    client.force_login(ctx["manager"])

    response = client.post(
        reverse(
            "scheduling_manager:lesson_reschedule",
            kwargs={"lesson_id": source.id},
        ),
        {
            "new_starts_at": local_target.strftime("%Y-%m-%dT%H:%M"),
            "new_ends_at": local_end.strftime("%Y-%m-%dT%H:%M"),
            "reason": Lesson.CancellationReason.ADMINISTRATIVE,
        },
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "scheduling_manager:lesson_detail",
        kwargs={"lesson_id": source.id},
    )
    source.refresh_from_db()
    assert source.status == Lesson.Status.RSVP_OPEN
    assert source.replacement_lesson_id is None
    assert Lesson.objects.filter(replaced_lesson=source).count() == 0
