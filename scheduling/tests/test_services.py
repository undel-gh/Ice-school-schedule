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


from accounts.models import Student, StudentAccess
from scheduling.models import (
    GroupMembership,
    LessonEnrollment,
    LessonResponse,
    LessonRosterEntry,
)
from scheduling.services import (
    cancel_lesson,
    confirm_lesson,
    evaluate_lesson_viability,
    publish_lesson,
    reschedule_lesson,
    set_lesson_response,
)
from subscriptions.models import (
    MakeupEntitlement,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import issue_subscription


@pytest.fixture
def student(db):
    return Student.objects.create(display_name="Student A")


@pytest.fixture
def second_student(db):
    return Student.objects.create(display_name="Student B")


@pytest.fixture
def guardian(db):
    return User.objects.create_user(username="guardian", password="test")


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        username="admin-scheduling",
        password="test",
        is_staff=True,
    )


def add_membership(*, student, group, starts_on, ends_on=None):
    return GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=starts_on,
        ends_on=ends_on,
    )


@pytest.mark.django_db
def test_publish_lesson_snapshots_group_and_enrollment(
    school_context,
    student,
    second_student,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    membership = add_membership(
        student=student,
        group=group,
        starts_on=date(2026, 9, 1),
    )
    enrollment = LessonEnrollment.objects.create(
        lesson=lesson,
        student=second_student,
        reason=LessonEnrollment.Reason.GUEST,
        created_by=coach_user,
    )

    published = publish_lesson(
        lesson_id=lesson.id,
        actor=coach_user,
        now=starts_at - timedelta(hours=3),
    )

    assert published.status == Lesson.Status.RSVP_OPEN
    group_roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=student,
    )
    enrollment_roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=second_student,
    )
    assert group_roster.source == LessonRosterEntry.Source.GROUP
    assert group_roster.group_membership_id == membership.id
    assert enrollment_roster.source == LessonRosterEntry.Source.ENROLLMENT
    assert enrollment_roster.lesson_enrollment_id == enrollment.id


@pytest.mark.django_db
def test_publish_lesson_excludes_expired_membership(
    school_context,
    student,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    add_membership(
        student=student,
        group=group,
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 9, 10),
    )

    publish_lesson(
        lesson_id=lesson.id,
        actor=coach_user,
        now=starts_at - timedelta(hours=3),
    )

    assert not LessonRosterEntry.objects.filter(
        lesson=lesson,
        student=student,
    ).exists()


@pytest.mark.django_db
def test_set_lesson_response_requires_student_access(
    school_context,
    student,
    guardian,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=1),
        decision_deadline=starts_at - timedelta(minutes=30),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
    )

    with pytest.raises(ValidationError):
        set_lesson_response(
            actor=guardian,
            student_id=student.id,
            lesson_id=lesson.id,
            status=LessonResponse.Status.YES,
            now=starts_at - timedelta(hours=2),
        )

    StudentAccess.objects.create(
        user=guardian,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    response = set_lesson_response(
        actor=guardian,
        student_id=student.id,
        lesson_id=lesson.id,
        status=LessonResponse.Status.YES,
        now=starts_at - timedelta(hours=2),
    )
    assert response.status == LessonResponse.Status.YES


@pytest.mark.django_db
def test_set_lesson_response_is_last_write_wins(
    school_context,
    student,
    guardian,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=1),
        decision_deadline=starts_at - timedelta(minutes=30),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
    )
    StudentAccess.objects.create(
        user=guardian,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )

    first = set_lesson_response(
        actor=guardian,
        student_id=student.id,
        lesson_id=lesson.id,
        status=LessonResponse.Status.YES,
        now=starts_at - timedelta(hours=2),
    )
    second = set_lesson_response(
        actor=guardian,
        student_id=student.id,
        lesson_id=lesson.id,
        status=LessonResponse.Status.NO,
        now=starts_at - timedelta(hours=2),
    )

    assert second.id == first.id
    assert second.status == LessonResponse.Status.NO
    assert LessonResponse.objects.filter(
        lesson=lesson,
        student=student,
    ).count() == 1


@pytest.mark.django_db
def test_evaluate_lesson_viability_snapshots_counts(
    school_context,
    student,
    second_student,
    guardian,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=2,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    for s in (student, second_student):
        LessonRosterEntry.objects.create(
            lesson=lesson,
            student=s,
            source=LessonRosterEntry.Source.MANUAL,
            added_by=coach_user,
        )
    LessonResponse.objects.create(
        lesson=lesson,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )

    result = evaluate_lesson_viability(
        lesson_id=lesson.id,
        now=starts_at - timedelta(minutes=30),
    )

    lesson.refresh_from_db()
    assert result.yes_count == 1
    assert result.no_count == 0
    assert result.no_response_count == 1
    assert result.minimum_met is False
    assert lesson.decision_yes_count == 1
    assert lesson.decision_no_response_count == 1


@pytest.mark.django_db
def test_confirm_lesson_does_not_require_minimum(
    school_context,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=4,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )

    confirmed = confirm_lesson(
        lesson_id=lesson.id,
        actor=admin,
        now=starts_at - timedelta(minutes=30),
    )

    assert confirmed.status == Lesson.Status.CONFIRMED
    assert confirmed.confirmed_by_id == admin.id


@pytest.mark.django_db
def test_cancel_lesson_records_reason(
    school_context,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.CONFIRMED,
    )

    cancelled = cancel_lesson(
        lesson_id=lesson.id,
        actor=admin,
        reason=Lesson.CancellationReason.LOW_ATTENDANCE,
        now=starts_at - timedelta(minutes=30),
    )

    assert cancelled.status == Lesson.Status.CANCELLED
    assert cancelled.cancellation_reason == Lesson.CancellationReason.LOW_ATTENDANCE


@pytest.mark.django_db
def test_reschedule_creates_draft_without_copying_rsvp(
    school_context,
    student,
    guardian,
    admin,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonResponse.objects.create(
        lesson=source,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )

    replacement = reschedule_lesson(
        lesson_id=source.id,
        new_starts_at=starts_at + timedelta(days=1),
        new_ends_at=starts_at + timedelta(days=1, hours=1),
        actor=admin,
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
    )

    source.refresh_from_db()
    assert source.status == Lesson.Status.CANCELLED
    assert source.replacement_lesson_id == replacement.id
    assert replacement.status == Lesson.Status.DRAFT
    assert replacement.responses.count() == 0


@pytest.mark.django_db
def test_reschedule_beyond_subscription_creates_targeted_makeup(
    school_context,
    student,
    guardian,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 29, 15, 0, tzinfo=dt_timezone.utc)
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonResponse.objects.create(
        lesson=source,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )
    plan = SubscriptionPlan.objects.create(
        code="reschedule-ice",
        name="Reschedule ICE",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category="ice",
        visit_limit=1,
    )
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=admin,
    )

    replacement = reschedule_lesson(
        lesson_id=source.id,
        new_starts_at=datetime(
            2026,
            10,
            2,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
        new_ends_at=datetime(
            2026,
            10,
            2,
            16,
            0,
            tzinfo=dt_timezone.utc,
        ),
        actor=admin,
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
    )

    makeup = MakeupEntitlement.objects.get(
        student=student,
        source_lesson=source,
        target_lesson=replacement,
    )
    assert makeup.reason == MakeupEntitlement.Reason.SCHOOL_RESCHEDULE
    assert makeup.source_subscription_allowance.subscription_id == subscription.id
    assert makeup.valid_from == date(2026, 10, 2)
    assert makeup.valid_until == date(2026, 10, 2)
