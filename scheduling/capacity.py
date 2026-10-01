from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db.models import Q

from .models import GroupMembership, GroupSeatReservation, TrainingGroup


@dataclass(frozen=True, slots=True)
class GroupCapacitySnapshot:
    capacity: int | None
    occupied: int

    @property
    def available(self) -> int | None:
        if self.capacity is None:
            return None
        return max(self.capacity - self.occupied, 0)


def group_occupied_student_ids(
    *,
    group_id: UUID,
    on_date: date,
    exclude_membership_id: UUID | None = None,
    exclude_reservation_id: UUID | None = None,
) -> set[UUID]:
    """
    Return distinct students claiming a group seat on one date.

    A student can have both a membership and a temporary seat reservation;
    that still consumes exactly one place.
    """
    memberships = GroupMembership.objects.filter(
        group_id=group_id,
        student__is_active=True,
        starts_on__lte=on_date,
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=on_date))
    if exclude_membership_id is not None:
        memberships = memberships.exclude(pk=exclude_membership_id)

    reservations = GroupSeatReservation.objects.filter(
        group_id=group_id,
        starts_on__lte=on_date,
        ends_on__gte=on_date,
        cancelled_at__isnull=True,
    )
    if exclude_reservation_id is not None:
        reservations = reservations.exclude(pk=exclude_reservation_id)

    return set(memberships.values_list("student_id", flat=True)) | set(
        reservations.values_list("student_id", flat=True)
    )


def group_capacity_snapshot(
    *,
    group: TrainingGroup,
    on_date: date,
) -> GroupCapacitySnapshot:
    occupied = len(
        group_occupied_student_ids(
            group_id=group.id,
            on_date=on_date,
        )
    )
    return GroupCapacitySnapshot(
        capacity=group.capacity,
        occupied=occupied,
    )
