from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import Student
from core.time import school_date
from scheduling.models import GroupMembership, GroupSeatReservation, TrainingGroup
from scheduling.services import (
    create_group_membership,
    update_training_group,
)


User = get_user_model()


@pytest.fixture
def capacity_manager(db):
    return User.objects.create_user(
        username="capacity-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_membership_admission_rejects_full_group(capacity_manager):
    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-one",
        name="Capacity one",
        capacity=1,
    )
    first = Student.objects.create(display_name="First")
    second = Student.objects.create(display_name="Second")
    GroupMembership.objects.create(
        student=first,
        group=group,
        starts_on=today,
        created_by=capacity_manager,
    )

    with pytest.raises(ValidationError) as exc:
        create_group_membership(
            student_id=second.id,
            group_id=group.id,
            starts_on=today,
            ends_on=None,
            actor=capacity_manager,
        )

    assert "capacity" in exc.value.message_dict
    assert GroupMembership.objects.filter(group=group).count() == 1


@pytest.mark.django_db
def test_inactive_student_membership_does_not_claim_group_capacity(
    capacity_manager,
):
    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-inactive-student",
        name="Capacity inactive student",
        capacity=2,
    )
    active = Student.objects.create(display_name="Active")
    inactive = Student.objects.create(
        display_name="Inactive",
        is_active=False,
    )
    newcomer = Student.objects.create(display_name="Newcomer")
    GroupMembership.objects.create(
        student=active,
        group=group,
        starts_on=today,
        created_by=capacity_manager,
    )
    GroupMembership.objects.create(
        student=inactive,
        group=group,
        starts_on=today,
        created_by=capacity_manager,
    )

    membership = create_group_membership(
        student_id=newcomer.id,
        group_id=group.id,
        starts_on=today,
        ends_on=None,
        actor=capacity_manager,
    )

    assert membership.student_id == newcomer.id


@pytest.mark.django_db
def test_inactive_student_paid_reservation_still_claims_group_capacity(
    capacity_manager,
):
    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-inactive-reserved",
        name="Capacity inactive reserved",
        capacity=1,
    )
    inactive = Student.objects.create(
        display_name="Inactive reserved",
        is_active=False,
    )
    newcomer = Student.objects.create(display_name="Newcomer")
    GroupSeatReservation.objects.create(
        student=inactive,
        group=group,
        starts_on=today,
        ends_on=today + timedelta(days=10),
        created_by=capacity_manager,
    )

    with pytest.raises(ValidationError) as exc:
        create_group_membership(
            student_id=newcomer.id,
            group_id=group.id,
            starts_on=today,
            ends_on=None,
            actor=capacity_manager,
        )

    assert "capacity" in exc.value.message_dict


@pytest.mark.django_db
def test_group_occupancy_uses_single_union_query(
    capacity_manager,
    django_assert_num_queries,
):
    from scheduling.capacity import group_occupied_student_ids

    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-query-count",
        name="Capacity query count",
        capacity=3,
    )
    member = Student.objects.create(display_name="Member")
    reserved = Student.objects.create(display_name="Reserved")
    GroupMembership.objects.create(
        student=member,
        group=group,
        starts_on=today,
        created_by=capacity_manager,
    )
    GroupSeatReservation.objects.create(
        student=reserved,
        group=group,
        starts_on=today,
        ends_on=today + timedelta(days=2),
        created_by=capacity_manager,
    )

    with django_assert_num_queries(1):
        occupied = group_occupied_student_ids(
            group_id=group.id,
            on_date=today,
        )

    assert occupied == {member.id, reserved.id}


@pytest.mark.django_db
def test_membership_capacity_checks_future_overlap(capacity_manager):
    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-future",
        name="Capacity future",
        capacity=1,
    )
    first = Student.objects.create(display_name="First")
    second = Student.objects.create(display_name="Second")
    GroupMembership.objects.create(
        student=first,
        group=group,
        starts_on=today + timedelta(days=10),
        ends_on=today + timedelta(days=20),
        created_by=capacity_manager,
    )

    with pytest.raises(ValidationError):
        create_group_membership(
            student_id=second.id,
            group_id=group.id,
            starts_on=today,
            ends_on=today + timedelta(days=30),
            actor=capacity_manager,
        )


@pytest.mark.django_db
def test_group_capacity_cannot_be_lowered_below_future_claims(capacity_manager):
    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-lower",
        name="Capacity lower",
        capacity=2,
    )
    for index in range(2):
        student = Student.objects.create(display_name=f"Student {index}")
        GroupMembership.objects.create(
            student=student,
            group=group,
            starts_on=today + timedelta(days=5),
            created_by=capacity_manager,
        )

    with pytest.raises(ValidationError) as exc:
        update_training_group(
            group_id=group.id,
            code=group.code,
            name=group.name,
            default_minimum_attendees=group.default_minimum_attendees,
            is_active=True,
            actor=capacity_manager,
            capacity=1,
        )

    assert "capacity" in exc.value.message_dict


@pytest.mark.django_db
def test_group_capacity_cannot_be_lower_than_minimum_attendees(capacity_manager):
    group = TrainingGroup.objects.create(
        code="capacity-minimum",
        name="Capacity minimum",
        default_minimum_attendees=2,
        capacity=2,
    )

    with pytest.raises(ValidationError) as exc:
        update_training_group(
            group_id=group.id,
            code=group.code,
            name=group.name,
            default_minimum_attendees=3,
            is_active=True,
            actor=capacity_manager,
            capacity=2,
        )

    assert "capacity" in exc.value.message_dict


@pytest.mark.django_db
def test_group_with_current_or_future_seat_reservation_cannot_be_deactivated(
    capacity_manager,
):
    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-reserved",
        name="Capacity reserved",
        capacity=2,
    )
    student = Student.objects.create(display_name="Reserved student")
    GroupSeatReservation.objects.create(
        student=student,
        group=group,
        starts_on=today,
        ends_on=today + timedelta(days=10),
        created_by=capacity_manager,
    )

    with pytest.raises(ValidationError) as exc:
        update_training_group(
            group_id=group.id,
            code=group.code,
            name=group.name,
            default_minimum_attendees=group.default_minimum_attendees,
            is_active=False,
            actor=capacity_manager,
            capacity=group.capacity,
        )

    assert "is_active" in exc.value.message_dict
    assert "seat reservation" in exc.value.message_dict["is_active"][0]


@pytest.mark.django_db
def test_expired_seat_reservation_does_not_block_group_deactivation(
    capacity_manager,
):
    today = school_date(timezone.now())
    group = TrainingGroup.objects.create(
        code="capacity-expired-reservation",
        name="Capacity expired reservation",
        capacity=2,
    )
    student = Student.objects.create(display_name="Past reserved student")
    GroupSeatReservation.objects.create(
        student=student,
        group=group,
        starts_on=today - timedelta(days=10),
        ends_on=today - timedelta(days=1),
        created_by=capacity_manager,
    )

    updated = update_training_group(
        group_id=group.id,
        code=group.code,
        name=group.name,
        default_minimum_attendees=group.default_minimum_attendees,
        is_active=False,
        actor=capacity_manager,
        capacity=group.capacity,
    )

    assert updated.is_active is False
