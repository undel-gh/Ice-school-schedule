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


def _student_with_membership(*, group, actor, name):
    period_from, _ = _hold_window()
    student = Student.objects.create(display_name=name)
    GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=period_from - timedelta(days=30),
        ends_on=None,
        created_by=actor,
    )
    return student


def _lesson(*, group, actor, lesson_date, suffix):
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


@pytest.mark.django_db
def test_active_place_hold_materializes_capacity_reservation(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-capacity-one",
        name="Hold capacity one",
        capacity=1,
    )
    holder = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Holder",
    )

    hold = create_group_place_hold(
        student_id=holder.id,
        group_id=group.id,
        period_scheme_id=rolling_scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=hold_manager,
    )
    hold = confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )

    reservation = GroupSeatReservation.objects.get(pk=hold.seat_reservation_id)
    assert reservation.student_id == holder.id
    assert reservation.group_id == group.id
    assert reservation.starts_on == period_from
    assert reservation.ends_on == period_until
    assert reservation.cancelled_at is None


@pytest.mark.django_db
def test_active_place_hold_suppresses_roster_and_membership_resumes_after_period(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-roster-overlay",
        name="Hold roster overlay",
        capacity=2,
    )
    holder = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Holder",
    )
    hold = create_group_place_hold(
        student_id=holder.id,
        group_id=group.id,
        period_scheme_id=rolling_scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=hold_manager,
    )
    confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )

    held_lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=period_from,
        suffix="held",
    )
    publish_lesson(
        lesson_id=held_lesson.id,
        actor=hold_manager,
        now=held_lesson.rsvp_deadline - timedelta(minutes=1),
    )
    assert not LessonRosterEntry.objects.filter(
        lesson=held_lesson,
        student=holder,
        is_active=True,
    ).exists()

    return_lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=period_until + timedelta(days=1),
        suffix="return",
    )
    publish_lesson(
        lesson_id=return_lesson.id,
        actor=hold_manager,
        now=return_lesson.rsvp_deadline - timedelta(minutes=1),
    )
    assert LessonRosterEntry.objects.filter(
        lesson=return_lesson,
        student=holder,
        is_active=True,
    ).exists()


@pytest.mark.django_db
def test_hold_activation_reconciles_already_published_group_roster(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-published-roster",
        name="Hold published roster",
        capacity=2,
    )
    holder = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Published holder",
    )
    hold = create_group_place_hold(
        student_id=holder.id,
        group_id=group.id,
        period_scheme_id=rolling_scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=hold_manager,
    )

    lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=period_from,
        suffix="published-before-hold",
    )
    publish_lesson(
        lesson_id=lesson.id,
        actor=hold_manager,
        now=lesson.rsvp_deadline - timedelta(minutes=1),
    )
    roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=holder,
    )
    assert roster.source == LessonRosterEntry.Source.GROUP
    assert roster.is_active is True

    activated_at = timezone.now()
    confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=activated_at,
    )
    roster.refresh_from_db()
    assert roster.is_active is False
    assert roster.deactivated_at == activated_at

    cancel_group_place_hold(
        hold_id=hold.id,
        actor=hold_manager,
        reason="Return to regular training",
        now=activated_at + timedelta(minutes=1),
    )
    roster.refresh_from_db()
    assert roster.is_active is True
    assert roster.deactivated_at is None


@pytest.mark.django_db
def test_hold_does_not_suppress_explicit_lesson_enrollment(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-explicit-enrollment",
        name="Hold explicit enrollment",
        capacity=2,
    )
    holder = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Explicit holder",
    )
    hold = create_group_place_hold(
        student_id=holder.id,
        group_id=group.id,
        period_scheme_id=rolling_scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=hold_manager,
    )

    lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=period_from,
        suffix="explicit",
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

    confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
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
def test_active_hold_reservation_still_blocks_capacity_if_membership_is_ended(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-reservation-capacity",
        name="Hold reservation capacity",
        capacity=1,
    )
    holder = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Holder",
    )
    other = Student.objects.create(display_name="Other")
    hold = create_group_place_hold(
        student_id=holder.id,
        group_id=group.id,
        period_scheme_id=rolling_scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=hold_manager,
    )
    confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )

    membership = GroupMembership.objects.get(student=holder, group=group)
    membership.ends_on = period_from - timedelta(days=1)
    membership.save(update_fields=["ends_on"])

    with pytest.raises(ValidationError):
        create_group_membership(
            student_id=other.id,
            group_id=group.id,
            starts_on=period_from,
            ends_on=period_until,
            actor=hold_manager,
        )


@pytest.mark.django_db
def test_cancelling_active_hold_removes_roster_suppression(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="cancelled-hold-overlay",
        name="Cancelled hold overlay",
        capacity=1,
    )
    holder = _student_with_membership(
        group=group,
        actor=hold_manager,
        name="Cancelled holder",
    )
    hold = create_group_place_hold(
        student_id=holder.id,
        group_id=group.id,
        period_scheme_id=rolling_scheme.id,
        period_from=period_from,
        period_until=period_until,
        actor=hold_manager,
    )
    hold = confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )
    reservation_id = hold.seat_reservation_id

    cancel_group_place_hold(
        hold_id=hold.id,
        actor=hold_manager,
        reason="No longer needed",
        now=timezone.now(),
    )

    reservation = GroupSeatReservation.objects.get(pk=reservation_id)
    assert reservation.cancelled_at is not None

    lesson = _lesson(
        group=group,
        actor=hold_manager,
        lesson_date=period_from,
        suffix="cancelled",
    )
    publish_lesson(
        lesson_id=lesson.id,
        actor=hold_manager,
        now=lesson.rsvp_deadline - timedelta(minutes=1),
    )
    assert LessonRosterEntry.objects.filter(
        lesson=lesson,
        student=holder,
        is_active=True,
    ).exists()


@pytest.mark.django_db
def test_place_hold_requires_membership_covering_full_period(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-membership-basis",
        name="Hold membership basis",
        capacity=3,
    )
    student = Student.objects.create(display_name="Short membership")
    GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=period_from - timedelta(days=30),
        ends_on=period_from + timedelta(days=5),
        created_by=hold_manager,
    )

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
