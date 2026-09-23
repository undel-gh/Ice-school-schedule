from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Sum

from attendance.models import Attendance
from scheduling.models import Lesson
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
)


@dataclass(frozen=True, slots=True)
class ClosedLessonReport:
    lesson: Lesson
    present_students: tuple
    absent_students: tuple
    present_count: int
    absent_count: int
    covered_count: int
    uncovered_count: int


@dataclass(frozen=True, slots=True)
class ExpiredAllowanceBalance:
    allowance: SubscriptionAllowance
    balance: int


@dataclass(frozen=True, slots=True)
class ExpiredSubscriptionReport:
    subscription: Subscription
    balances: tuple[ExpiredAllowanceBalance, ...]
    available_makeups: tuple[MakeupEntitlement, ...]


def get_closed_lesson_report(*, lesson_id: UUID) -> ClosedLessonReport:
    lesson = (
        Lesson.objects.select_related("coach", "lesson_type")
        .get(pk=lesson_id)
    )
    if lesson.status != Lesson.Status.CLOSED:
        raise ValidationError(
            {"lesson": "Financial report is available only for CLOSED lessons."}
        )

    active_coverage = AttendanceCoverage.objects.filter(
        attendance_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    records = list(
        Attendance.objects.filter(lesson=lesson)
        .select_related("student")
        .annotate(has_active_coverage=Exists(active_coverage))
        .order_by("student__display_name", "student_id")
    )

    present = tuple(
        record.student
        for record in records
        if record.status == Attendance.Status.PRESENT
    )
    absent = tuple(
        record.student
        for record in records
        if record.status == Attendance.Status.ABSENT
    )
    covered_count = sum(
        1
        for record in records
        if (
            record.status == Attendance.Status.PRESENT
            and record.has_active_coverage
        )
    )
    uncovered_count = sum(
        1
        for record in records
        if (
            record.status == Attendance.Status.PRESENT
            and not record.has_active_coverage
        )
    )

    return ClosedLessonReport(
        lesson=lesson,
        present_students=present,
        absent_students=absent,
        present_count=len(present),
        absent_count=len(absent),
        covered_count=covered_count,
        uncovered_count=uncovered_count,
    )


def get_uncovered_attendance():
    active_coverage = AttendanceCoverage.objects.filter(
        attendance_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    return (
        Attendance.objects.filter(status=Attendance.Status.PRESENT)
        .annotate(has_active_coverage=Exists(active_coverage))
        .filter(has_active_coverage=False)
        .select_related(
            "student",
            "lesson",
            "lesson__lesson_type",
            "lesson__coach",
        )
        .order_by("lesson__starts_at", "student__display_name", "student_id")
    )


def get_expired_unused_report(
    *,
    as_of: date,
) -> tuple[ExpiredSubscriptionReport, ...]:
    subscriptions = list(
        Subscription.objects.filter(
            valid_until__lt=as_of,
            cancelled_at__isnull=True,
        )
        .select_related("student", "plan")
        .order_by("student__display_name", "valid_until", "created_at")
    )
    if not subscriptions:
        return ()

    subscription_ids = [subscription.id for subscription in subscriptions]
    allowances = list(
        SubscriptionAllowance.objects.filter(
            subscription_id__in=subscription_ids,
        )
        .annotate(balance=Sum("ledger_entries__delta"))
        .order_by("subscription_id", "category")
    )

    positive_by_subscription: dict[UUID, list[ExpiredAllowanceBalance]] = {}
    positive_allowance_ids: list[UUID] = []
    for allowance in allowances:
        balance = int(allowance.balance or 0)
        if balance <= 0:
            continue
        positive_by_subscription.setdefault(
            allowance.subscription_id,
            [],
        ).append(
            ExpiredAllowanceBalance(
                allowance=allowance,
                balance=balance,
            )
        )
        positive_allowance_ids.append(allowance.id)

    if not positive_allowance_ids:
        return ()

    makeups = list(
        MakeupEntitlement.objects.filter(
            source_subscription_allowance_id__in=positive_allowance_ids,
            cancelled_at__isnull=True,
            valid_until__gte=as_of,
        )
        .select_related("source_subscription_allowance")
        .order_by("valid_until", "created_at")
    )
    makeups_by_subscription: dict[UUID, list[MakeupEntitlement]] = {}
    for makeup in makeups:
        subscription_id = makeup.source_subscription_allowance.subscription_id
        makeups_by_subscription.setdefault(subscription_id, []).append(makeup)

    reports: list[ExpiredSubscriptionReport] = []
    for subscription in subscriptions:
        balances = positive_by_subscription.get(subscription.id)
        if not balances:
            continue
        reports.append(
            ExpiredSubscriptionReport(
                subscription=subscription,
                balances=tuple(balances),
                available_makeups=tuple(
                    makeups_by_subscription.get(subscription.id, ())
                ),
            )
        )

    return tuple(reports)
