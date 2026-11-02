from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.models import CoachProfile
from core.testing import school_dt
from ice_school.workflows import reschedule_lesson_with_entitlements
from scheduling.models import Lesson, LessonType, ScheduleTemplate, TrainingGroup, Venue
from scheduling.services import generate_lessons, reschedule_lesson


User = get_user_model()


@pytest.fixture
def resource_context(db):
    actor = User.objects.create_superuser(
        username="resource-conflict-manager",
        password="test-password",
    )
    coach_a_user = User.objects.create_user(username="resource-coach-a")
    coach_b_user = User.objects.create_user(username="resource-coach-b")
    coach_a = CoachProfile.objects.create(
        user=coach_a_user,
        display_name="Coach A",
    )
    coach_b = CoachProfile.objects.create(
        user=coach_b_user,
        display_name="Coach B",
    )
    group_a = TrainingGroup.objects.create(code="resource-a", name="Group A")
    group_b = TrainingGroup.objects.create(code="resource-b", name="Group B")
    venue_a = Venue.objects.create(code="resource-rink-a", name="Rink A")
    venue_b = Venue.objects.create(code="resource-rink-b", name="Rink B")
    lesson_type = LessonType.objects.create(
        code="resource-ice",
        name="Ice",
        subscription_category="ice",
    )
    return {
        "actor": actor,
        "coach_a": coach_a,
        "coach_b": coach_b,
        "group_a": group_a,
        "group_b": group_b,
        "venue_a": venue_a,
        "venue_b": venue_b,
        "lesson_type": lesson_type,
    }


def _lesson(
    *,
    group,
    coach,
    venue,
    lesson_type,
    starts_at,
    status=Lesson.Status.DRAFT,
):
    return Lesson.objects.create(
        group=group,
        coach=coach,
        venue=venue,
        lesson_type=lesson_type,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=status,
    )


def _reschedule(source, *, actor, starts_at):
    return reschedule_lesson(
        lesson_id=source.id,
        new_starts_at=starts_at,
        new_ends_at=starts_at + timedelta(hours=1),
        actor=actor,
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=starts_at - timedelta(days=1),
    )


def _source_and_target_times():
    return school_dt(2099, 1, 4, 10, 0), school_dt(2099, 1, 5, 10, 0)


@pytest.mark.django_db
def test_reschedule_rejects_overlapping_coach_across_groups(resource_context):
    ctx = resource_context
    source_start, target_start = _source_and_target_times()
    source = _lesson(
        group=ctx["group_a"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=source_start,
    )
    conflict = _lesson(
        group=ctx["group_b"],
        coach=ctx["coach_a"],
        venue=ctx["venue_b"],
        lesson_type=ctx["lesson_type"],
        starts_at=target_start,
    )

    with pytest.raises(ValidationError) as exc_info:
        _reschedule(source, actor=ctx["actor"], starts_at=target_start)

    assert str(conflict.id) in str(exc_info.value)
    assert "coach" in str(exc_info.value).lower()
    source.refresh_from_db()
    assert source.status == Lesson.Status.DRAFT
    assert source.replacement_lesson_id is None


@pytest.mark.django_db
def test_reschedule_rejects_overlapping_venue_across_groups(resource_context):
    ctx = resource_context
    source_start, target_start = _source_and_target_times()
    source = _lesson(
        group=ctx["group_a"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=source_start,
    )
    conflict = _lesson(
        group=ctx["group_b"],
        coach=ctx["coach_b"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=target_start,
    )

    with pytest.raises(ValidationError) as exc_info:
        _reschedule(source, actor=ctx["actor"], starts_at=target_start)

    assert str(conflict.id) in str(exc_info.value)
    assert "venue" in str(exc_info.value).lower()
    source.refresh_from_db()
    assert source.status == Lesson.Status.DRAFT
    assert source.replacement_lesson_id is None


@pytest.mark.django_db
def test_reschedule_keeps_existing_group_conflict_precedence(resource_context):
    ctx = resource_context
    source_start, target_start = _source_and_target_times()
    source = _lesson(
        group=ctx["group_a"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=source_start,
    )
    conflict = _lesson(
        group=ctx["group_a"],
        coach=ctx["coach_b"],
        venue=ctx["venue_b"],
        lesson_type=ctx["lesson_type"],
        starts_at=target_start,
    )

    with pytest.raises(ValidationError) as exc_info:
        _reschedule(source, actor=ctx["actor"], starts_at=target_start)

    assert str(conflict.id) in str(exc_info.value)
    assert "this group" in str(exc_info.value)


@pytest.mark.django_db
def test_reschedule_ignores_cancelled_resource_conflict(resource_context):
    ctx = resource_context
    source_start, target_start = _source_and_target_times()
    source = _lesson(
        group=ctx["group_a"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=source_start,
    )
    _lesson(
        group=ctx["group_b"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=target_start,
        status=Lesson.Status.CANCELLED,
    )

    replacement = _reschedule(
        source,
        actor=ctx["actor"],
        starts_at=target_start,
    )

    source.refresh_from_db()
    assert source.status == Lesson.Status.CANCELLED
    assert source.replacement_lesson_id == replacement.id
    assert replacement.coach_id == ctx["coach_a"].id
    assert replacement.venue_id == ctx["venue_a"].id


@pytest.mark.django_db
def test_canonical_reschedule_workflow_rejects_cross_group_coach_conflict(
    resource_context,
):
    ctx = resource_context
    source_start, target_start = _source_and_target_times()
    source = _lesson(
        group=ctx["group_a"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=source_start,
    )
    _lesson(
        group=ctx["group_b"],
        coach=ctx["coach_a"],
        venue=ctx["venue_b"],
        lesson_type=ctx["lesson_type"],
        starts_at=target_start,
    )

    with pytest.raises(ValidationError):
        reschedule_lesson_with_entitlements(
            lesson_id=source.id,
            new_starts_at=target_start,
            new_ends_at=target_start + timedelta(hours=1),
            actor=ctx["actor"],
            reason=Lesson.CancellationReason.ADMINISTRATIVE,
            now=target_start - timedelta(days=1),
        )

    source.refresh_from_db()
    assert source.status == Lesson.Status.DRAFT
    assert source.replacement_lesson_id is None
    assert not Lesson.objects.filter(replaced_lesson=source).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("resource", ["coach", "venue"])
def test_generate_lessons_reports_cross_group_resource_conflict(
    resource_context,
    resource,
):
    ctx = resource_context
    occurrence_date = date(2099, 1, 5)
    starts_at = school_dt(2099, 1, 5, 10, 0)
    template = ScheduleTemplate.objects.create(
        group=ctx["group_a"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        weekday=occurrence_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        valid_from=occurrence_date,
        valid_until=occurrence_date,
        is_active=True,
    )
    conflict = _lesson(
        group=ctx["group_b"],
        coach=(ctx["coach_a"] if resource == "coach" else ctx["coach_b"]),
        venue=(ctx["venue_b"] if resource == "coach" else ctx["venue_a"]),
        lesson_type=ctx["lesson_type"],
        starts_at=starts_at,
    )

    result = generate_lessons(
        template_id=template.id,
        from_date=occurrence_date,
        until_date=occurrence_date,
        actor=ctx["actor"],
    )

    assert result.lessons == ()
    assert len(result.conflicts) == 1
    assert result.conflicts[0].conflicting_lesson_id == conflict.id
    assert result.conflicts[0].conflict_resource == resource
    assert not Lesson.objects.filter(source_template=template).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("resource", ["coach", "venue"])
def test_reschedule_preflight_rejects_unmaterialized_cross_group_resource_slot(
    resource_context,
    resource,
):
    ctx = resource_context
    source_start, target_start = _source_and_target_times()
    target_date = date(2099, 1, 5)
    source = _lesson(
        group=ctx["group_a"],
        coach=ctx["coach_a"],
        venue=ctx["venue_a"],
        lesson_type=ctx["lesson_type"],
        starts_at=source_start,
    )
    ScheduleTemplate.objects.create(
        group=ctx["group_b"],
        coach=(ctx["coach_a"] if resource == "coach" else ctx["coach_b"]),
        venue=(ctx["venue_b"] if resource == "coach" else ctx["venue_a"]),
        lesson_type=ctx["lesson_type"],
        weekday=target_date.weekday(),
        start_time=time(10, 0),
        duration_minutes=60,
        valid_from=target_date,
        valid_until=target_date,
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc_info:
        reschedule_lesson_with_entitlements(
            lesson_id=source.id,
            new_starts_at=target_start,
            new_ends_at=target_start + timedelta(hours=1),
            actor=ctx["actor"],
            reason=Lesson.CancellationReason.ADMINISTRATIVE,
            now=target_start - timedelta(days=1),
        )

    assert "unmaterialized active schedule template occurrence" in str(
        exc_info.value
    )
    source.refresh_from_db()
    assert source.status == Lesson.Status.DRAFT
    assert source.replacement_lesson_id is None
