from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import Student
from core.time import school_date
from scheduling.models import GroupMembership, TrainingGroup
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
