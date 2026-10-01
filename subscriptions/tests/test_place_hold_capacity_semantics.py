from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import CoachProfile, Student
from core.choices import SubscriptionCategory
from core.testing import school_dt
from core.time import school_date
from scheduling.capacity import group_occupied_student_ids
from scheduling.models import (
    GroupMembership,
    GroupSeatReservation,
    Lesson,
    LessonRosterEntry,
    LessonType,
    TrainingGroup,
    Venue,
)
from scheduling.services import (
    add_lesson_enrollment,
    create_group_membership,
    publish_lesson,
)
from subscriptions.models import GroupPlaceHold, SubscriptionPeriodScheme
from subscriptions.services import (
    cancel_group_place_hold,
    confirm_group_place_hold_fee,
    create_group_place_hold,
    process_subscription_lifecycle,
    restore_group_place_hold,
)


User = get_user_model()


@pytest.fixture
def hold_manager(db):
    return User.objects.create_user(
        username="hold-capacity-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def rolling_scheme(db):
    return SubscriptionPeriodScheme.objects.create(
        code="rolling-hold-capacity",
        name="Rolling hold capacity",
        mode=SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
        is_active=True,
    )


def _hold_window():
    period_from = school_date(timezone.now()) + timedelta(days=1)
    return period_from, period_from + timedelta(days=27)


def _student_with_membership(*, group, actor, name, ends_on=None):
    period_from, _ = _hold_window()
    student = Student.objects.create(display_name=name)
    membership = GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=period_from - timedelta(days=30),
        ends_on=ends_on,
        created_by=actor,
    )
    return student, membership


def _lesson(*, group, actor, lesson_date):
    coach = getattr(actor, "coach_profile", None)
    if coach is None:
        coach = CoachProfile.objects.create(
            user=actor,
            display_name="Hold coach",
        )
    venue, _ = Venue.objects.get_or_create(
        code="hold-capacity-rink",
        defaults={"name": "Hold capacity rink"},
    )
    lesson_type, _ = LessonType.objects.get_or_create(
        code="hold-capacity-ice",
        defaults={
            "name": "Hold capacity ice",
            "subscription_category": SubscriptionCategory.ICE,
        },
    )
    starts_at = school_dt(
        lesson_date.year,
        lesson_date.month,
        lesson_date.day,
        18,
        0,
    )
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
        status=Lesson.Status.DRAFT,
    )


def _activate_hold(*, holder, group, scheme, actor):
    period_from, period_until = _hold_window()
    hold = create_group_place_hold(
        student_id=holder.id,
        group_id=group.id,
        period_scheme_id=scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=actor,
    )
    return confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=actor,
        now=timezone.now(),
    )


@pytest.mark.django_db
def test_activation_suspends_membership_and_reserves_return_seat(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    return_on = period_until + timedelta(days=1)
    group = TrainingGroup.objects.create(
        code="hold-suspend",
        name="Hold suspend",
        capacity=1,
    )
    holder, membership = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Holder",
    )

    hold = _activate_hold(
        holder=holder,
        group=group,
        scheme=rolling_scheme,
        actor=hold_manager,
    )

    membership.refresh_from_db()
    hold.refresh_from_db()
    reservation = GroupSeatReservation.objects.get(
        pk=hold.seat_reservation_id
    )

    assert membership.ends_on == period_from - timedelta(days=1)
    assert hold.suspended_membership_id == membership.id
    assert reservation.starts_on == period_from
    assert reservation.ends_on == return_on
    assert holder.id in group_occupied_student_ids(
        group_id=group.id,
        on_date=period_from,
    )
    assert holder.id in group_occupied_student_ids(
        group_id=group.id,
        on_date=return_on,
    )


@pytest.mark.django_db
def test_restore_consumes_hold_and_is_allowed_at_formal_capacity(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    return_on = period_until + timedelta(days=1)
    group = TrainingGroup.objects.create(
        code="hold-restore-full",
        name="Hold restore full",
        capacity=1,
    )
    holder, _ = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Holder",
    )
    other = Student.objects.create(display_name="Other")
    hold = _activate_hold(
        holder=holder,
        group=group,
        scheme=rolling_scheme,
        actor=hold_manager,
    )

    with pytest.raises(ValidationError):
        create_group_membership(
            student_id=other.id,
            group_id=group.id,
            starts_on=return_on,
            ends_on=None,
            actor=hold_manager,
        )

    restored = restore_group_place_hold(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )

    assert restored.status == GroupPlaceHold.Status.RESTORED
    assert restored.restored_membership_id is not None
    membership = GroupMembership.objects.get(
        pk=restored.restored_membership_id
    )
    assert membership.starts_on == return_on
    assert membership.ends_on is None
    assert len(
        group_occupied_student_ids(
            group_id=group.id,
            on_date=return_on,
        )
    ) == 1

    with pytest.raises(ValidationError):
        create_group_membership(
            student_id=other.id,
            group_id=group.id,
            starts_on=return_on,
            ends_on=None,
            actor=hold_manager,
        )


@pytest.mark.django_db
def test_direct_membership_create_cannot_bypass_place_hold_restore(
    hold_manager,
    rolling_scheme,
):
    _, period_until = _hold_window()
    return_on = period_until + timedelta(days=1)
    group = TrainingGroup.objects.create(
        code="hold-no-bypass",
        name="Hold no bypass",
        capacity=2,
    )
    holder, _ = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Holder",
    )
    hold = _activate_hold(
        holder=holder,
        group=group,
        scheme=rolling_scheme,
        actor=hold_manager,
    )

    with pytest.raises(ValidationError, match="restore flow"):
        create_group_membership(
            student_id=holder.id,
            group_id=group.id,
            starts_on=return_on,
            ends_on=None,
            actor=hold_manager,
        )

    restored = restore_group_place_hold(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )
    assert restored.restored_membership.starts_on == return_on


@pytest.mark.django_db
def test_activation_and_restore_reconcile_already_published_rosters(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    return_on = period_until + timedelta(days=1)
    group = TrainingGroup.objects.create(
        code="hold-roster-lifecycle",
        name="Hold roster lifecycle",
        capacity=2,
    )
    holder, old_membership = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Holder",
    )

    held_lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=period_from,
    )
    return_lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=return_on,
    )
    for lesson in (held_lesson, return_lesson):
        publish_lesson(
            lesson_id=lesson.id,
            actor=hold_manager,
            now=lesson.rsvp_deadline - timedelta(minutes=1),
        )

    assert LessonRosterEntry.objects.get(
        lesson=held_lesson,
        student=holder,
    ).is_active
    assert LessonRosterEntry.objects.get(
        lesson=return_lesson,
        student=holder,
    ).is_active

    hold = _activate_hold(
        holder=holder,
        group=group,
        scheme=rolling_scheme,
        actor=hold_manager,
    )

    held_roster = LessonRosterEntry.objects.get(
        lesson=held_lesson,
        student=holder,
    )
    return_roster = LessonRosterEntry.objects.get(
        lesson=return_lesson,
        student=holder,
    )
    assert held_roster.is_active is False
    assert return_roster.is_active is False
    assert held_roster.group_membership_id == old_membership.id

    restored = restore_group_place_hold(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )
    held_roster.refresh_from_db()
    return_roster.refresh_from_db()

    assert held_roster.is_active is False
    assert return_roster.is_active is True
    assert (
        return_roster.group_membership_id
        == restored.restored_membership_id
    )


@pytest.mark.django_db
def test_explicit_lesson_enrollment_survives_hold_suspension(
    hold_manager,
    rolling_scheme,
):
    period_from, _ = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-explicit-enrollment",
        name="Hold explicit enrollment",
        capacity=2,
    )
    holder, _ = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Explicit holder",
    )
    lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=period_from,
    )
    add_lesson_enrollment(
        lesson_id=lesson.id,
        student_id=holder.id,
        reason="administrative",
        actor=hold_manager,
    )
    publish_lesson(
        lesson_id=lesson.id,
        actor=hold_manager,
        now=lesson.rsvp_deadline - timedelta(minutes=1),
    )

    _activate_hold(
        holder=holder,
        group=group,
        scheme=rolling_scheme,
        actor=hold_manager,
    )

    roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=holder,
    )
    assert roster.is_active is True
    assert lesson.enrollments.filter(
        student=holder,
        cancelled_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_cancelling_active_hold_frees_seat_without_restoring_membership(
    hold_manager,
    rolling_scheme,
):
    period_from, _ = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-cancel-free",
        name="Hold cancel free",
        capacity=1,
    )
    holder, old_membership = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Cancelled holder",
    )
    other = Student.objects.create(display_name="Other")
    hold = _activate_hold(
        holder=holder,
        group=group,
        scheme=rolling_scheme,
        actor=hold_manager,
    )
    reservation_id = hold.seat_reservation_id

    cancelled = cancel_group_place_hold(
        hold_id=hold.id,
        actor=hold_manager,
        reason="No longer needed",
        now=timezone.now(),
    )

    assert cancelled.status == GroupPlaceHold.Status.CANCELLED
    reservation = GroupSeatReservation.objects.get(pk=reservation_id)
    assert reservation.cancelled_at is not None
    old_membership.refresh_from_db()
    assert old_membership.ends_on == period_from - timedelta(days=1)
    assert not GroupMembership.objects.filter(
        student=holder,
        group=group,
        ends_on__isnull=True,
    ).exists()

    admitted = create_group_membership(
        student_id=other.id,
        group_id=group.id,
        starts_on=period_from,
        ends_on=None,
        actor=hold_manager,
    )
    assert admitted.student_id == other.id


@pytest.mark.django_db
def test_expired_hold_frees_seat_and_does_not_restore_membership(
    hold_manager,
    rolling_scheme,
):
    _, period_until = _hold_window()
    return_on = period_until + timedelta(days=1)
    group = TrainingGroup.objects.create(
        code="hold-expire-free",
        name="Hold expire free",
        capacity=1,
    )
    holder, _ = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Expired holder",
    )
    other = Student.objects.create(display_name="Other")
    hold = _activate_hold(
        holder=holder,
        group=group,
        scheme=rolling_scheme,
        actor=hold_manager,
    )

    result = process_subscription_lifecycle(
        as_of=return_on + timedelta(days=1),
        actor=hold_manager,
    )

    hold.refresh_from_db()
    hold.seat_reservation.refresh_from_db()
    assert result["group_place_hold_expired"] == 1
    assert hold.status == GroupPlaceHold.Status.EXPIRED
    assert hold.seat_reservation.cancelled_at is not None
    assert hold.restored_membership_id is None

    admitted = create_group_membership(
        student_id=other.id,
        group_id=group.id,
        starts_on=return_on + timedelta(days=1),
        ends_on=None,
        actor=hold_manager,
    )
    assert admitted.student_id == other.id


@pytest.mark.django_db
def test_place_hold_requires_membership_active_at_start_not_full_period(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-start-membership",
        name="Hold start membership",
        capacity=3,
    )
    student, membership = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Short membership",
        ends_on=period_from + timedelta(days=5),
    )

    hold = create_group_place_hold(
        student_id=student.id,
        group_id=group.id,
        period_scheme_id=rolling_scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=hold_manager,
    )
    activated = confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )

    membership.refresh_from_db()
    assert activated.status == GroupPlaceHold.Status.ACTIVE
    assert membership.ends_on == period_from - timedelta(days=1)


@pytest.mark.django_db
def test_place_hold_rejects_student_without_membership_at_hold_start(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-membership-basis",
        name="Hold membership basis",
        capacity=3,
    )
    student = Student.objects.create(display_name="No membership")

    with pytest.raises(ValidationError) as exc:
        create_group_place_hold(
            student_id=student.id,
            group_id=group.id,
            period_scheme_id=rolling_scheme.id,
            period_from=period_from,
            period_until=period_until,
            actor=hold_manager,
        )

    assert "membership" in exc.value.message_dict
