from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import Student
from core.time import school_date
from scheduling.models import GroupMembership, GroupSeatReservation, TrainingGroup
from scheduling.services import create_group_membership
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


def _student_with_membership_before_hold(*, group, actor, name):
    period_from, _ = _hold_window()
    student = Student.objects.create(display_name=name)
    GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=period_from - timedelta(days=30),
        ends_on=period_from - timedelta(days=1),
        created_by=actor,
    )
    return student


@pytest.mark.django_db
def test_active_place_hold_blocks_other_student_admission_and_allows_return(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="hold-capacity-one",
        name="Hold capacity one",
        capacity=1,
    )
    holder = _student_with_membership_before_hold(
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
    hold = confirm_group_place_hold_fee(
        hold_id=hold.id,
        actor=hold_manager,
        now=timezone.now(),
    )

    reservation = GroupSeatReservation.objects.get(pk=hold.seat_reservation_id)
    assert reservation.starts_on == period_from
    assert reservation.ends_on == period_until + timedelta(days=1)

    with pytest.raises(ValidationError):
        create_group_membership(
            student_id=other.id,
            group_id=group.id,
            starts_on=period_from,
            ends_on=None,
            actor=hold_manager,
        )

    returning = create_group_membership(
        student_id=holder.id,
        group_id=group.id,
        starts_on=period_until + timedelta(days=1),
        ends_on=None,
        actor=hold_manager,
    )
    assert returning.student_id == holder.id


@pytest.mark.django_db
def test_pending_hold_does_not_reserve_capacity_but_activation_rechecks_it(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="pending-hold-capacity",
        name="Pending hold capacity",
        capacity=1,
    )
    holder = _student_with_membership_before_hold(
        group=group,
        actor=hold_manager,
        name="Pending holder",
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
    create_group_membership(
        student_id=other.id,
        group_id=group.id,
        starts_on=period_from,
        ends_on=None,
        actor=hold_manager,
    )

    with pytest.raises(ValidationError):
        confirm_group_place_hold_fee(
            hold_id=hold.id,
            actor=hold_manager,
            now=timezone.now(),
        )

    hold.refresh_from_db()
    assert hold.status == GroupPlaceHold.Status.PENDING_PAYMENT
    assert hold.seat_reservation_id is None


@pytest.mark.django_db
def test_cancelling_active_hold_releases_capacity(
    hold_manager,
    rolling_scheme,
):
    period_from, period_until = _hold_window()
    group = TrainingGroup.objects.create(
        code="cancelled-hold-capacity",
        name="Cancelled hold capacity",
        capacity=1,
    )
    holder = _student_with_membership_before_hold(
        group=group,
        actor=hold_manager,
        name="Cancelled holder",
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
    membership = create_group_membership(
        student_id=other.id,
        group_id=group.id,
        starts_on=period_from,
        ends_on=None,
        actor=hold_manager,
    )
    assert membership.student_id == other.id


@pytest.mark.django_db
def test_place_hold_requires_membership_basis(
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
