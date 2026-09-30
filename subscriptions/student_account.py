from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from django.db.models import Exists, OuterRef, Prefetch, Sum

from attendance.models import Attendance
from scheduling.models import Lesson

from .models import (
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
)
from .selectors import subscription_state


SUBSCRIPTION_STATE_LABELS = {
    "active": "Действует",
    "pending": "Ожидает первого занятия",
    "upcoming": "Ещё не начался",
    "expired": "Истёк",
    "cancelled": "Отменён",
}

RIGHT_STATUS_LABELS = {
    "available": "Доступно",
    "used": "Использовано",
    "cancelled": "Отменено",
    "expired": "Истекло",
    "not_started": "Действует позже",
    "lesson_cancelled": "Занятие отменено",
    "target_cancelled": "Целевое занятие отменено",
    "source_unavailable": "Нет доступного остатка",
}


@dataclass(frozen=True, slots=True)
class StudentAllowanceBalance:
    allowance: SubscriptionAllowance
    balance: int


@dataclass(frozen=True, slots=True)
class StudentSubscriptionView:
    subscription: Subscription
    state: str
    state_label: str
    allowances: tuple[StudentAllowanceBalance, ...]


@dataclass(frozen=True, slots=True)
class StudentOneTimeRight:
    entitlement: OneTimeEntitlement
    status: str
    status_label: str


@dataclass(frozen=True, slots=True)
class StudentMakeupRight:
    entitlement: MakeupEntitlement
    status: str
    status_label: str


@dataclass(frozen=True, slots=True)
class StudentAccountSnapshot:
    subscriptions: tuple[StudentSubscriptionView, ...]
    one_time_rights: tuple[StudentOneTimeRight, ...]
    makeup_rights: tuple[StudentMakeupRight, ...]


def _subscription_views(
    *,
    student_id: UUID,
    as_of: date,
) -> tuple[StudentSubscriptionView, ...]:
    allowance_queryset = (
        SubscriptionAllowance.objects.annotate(
            account_balance=Sum("ledger_entries__delta"),
        )
        .order_by("category", "id")
    )
    subscriptions = list(
        Subscription.objects.filter(student_id=student_id)
        .select_related("plan")
        .prefetch_related(
            Prefetch(
                "allowances",
                queryset=allowance_queryset,
                to_attr="account_allowances",
            )
        )
        .order_by("-created_at", "id")
    )

    rows = []
    for subscription in subscriptions:
        state = subscription_state(
            subscription=subscription,
            as_of=as_of,
        )
        allowances = tuple(
            StudentAllowanceBalance(
                allowance=allowance,
                balance=int(allowance.account_balance or 0),
            )
            for allowance in subscription.account_allowances
        )
        rows.append(
            StudentSubscriptionView(
                subscription=subscription,
                state=state,
                state_label=SUBSCRIPTION_STATE_LABELS[state],
                allowances=allowances,
            )
        )

    priority = {
        "active": 0,
        "pending": 1,
        "upcoming": 2,
        "expired": 3,
        "cancelled": 4,
    }
    rows.sort(
        key=lambda row: (
            priority[row.state],
            -row.subscription.created_at.timestamp(),
            str(row.subscription.id),
        )
    )
    return tuple(rows)


def _one_time_rights(
    *,
    student_id: UUID,
    now: datetime,
) -> tuple[StudentOneTimeRight, ...]:
    active_usage = AttendanceCoverage.objects.filter(
        one_time_entitlement_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    entitlements = list(
        OneTimeEntitlement.objects.filter(student_id=student_id)
        .annotate(account_is_used=Exists(active_usage))
        .select_related(
            "lesson",
            "lesson__lesson_type",
            "lesson__venue",
        )
        .order_by("-lesson__starts_at", "-created_at", "id")
    )

    rows = []
    for entitlement in entitlements:
        if entitlement.cancelled_at is not None:
            status = "cancelled"
        elif entitlement.account_is_used:
            status = "used"
        elif entitlement.lesson.status == Lesson.Status.CANCELLED:
            status = "lesson_cancelled"
        elif entitlement.lesson.ends_at < now:
            status = "expired"
        else:
            status = "available"
        rows.append(
            StudentOneTimeRight(
                entitlement=entitlement,
                status=status,
                status_label=RIGHT_STATUS_LABELS[status],
            )
        )

    priority = {
        "available": 0,
        "used": 1,
        "expired": 2,
        "lesson_cancelled": 3,
        "cancelled": 4,
    }
    rows.sort(
        key=lambda row: (
            priority[row.status],
            row.entitlement.lesson.starts_at,
            str(row.entitlement.id),
        )
    )
    return tuple(rows)


def _makeup_rights(
    *,
    student_id: UUID,
    as_of: date,
) -> tuple[StudentMakeupRight, ...]:
    active_usage = AttendanceCoverage.objects.filter(
        makeup_entitlement_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    entitlements = list(
        MakeupEntitlement.objects.filter(student_id=student_id)
        .annotate(account_is_used=Exists(active_usage))
        .select_related(
            "source_lesson",
            "source_lesson__lesson_type",
            "source_subscription_allowance",
            "source_subscription_allowance__subscription",
            "target_lesson",
            "target_lesson__lesson_type",
        )
        .order_by("-created_at", "id")
    )

    allowance_ids = {
        entitlement.source_subscription_allowance_id
        for entitlement in entitlements
    }
    balances = {
        row["allowance_id"]: int(row["balance"] or 0)
        for row in (
            SubscriptionLedgerEntry.objects.filter(
                allowance_id__in=allowance_ids
            )
            .values("allowance_id")
            .annotate(balance=Sum("delta"))
        )
    }

    rows = []
    for entitlement in entitlements:
        source_subscription = (
            entitlement.source_subscription_allowance.subscription
        )
        source_balance = balances.get(
            entitlement.source_subscription_allowance_id,
            0,
        )
        if entitlement.cancelled_at is not None:
            status = "cancelled"
        elif entitlement.account_is_used:
            status = "used"
        elif entitlement.valid_until < as_of:
            status = "expired"
        elif entitlement.valid_from > as_of:
            status = "not_started"
        elif (
            entitlement.target_lesson_id is not None
            and entitlement.target_lesson.status == Lesson.Status.CANCELLED
        ):
            status = "target_cancelled"
        elif (
            source_subscription.cancelled_at is not None
            or source_balance <= 0
        ):
            status = "source_unavailable"
        else:
            status = "available"

        rows.append(
            StudentMakeupRight(
                entitlement=entitlement,
                status=status,
                status_label=RIGHT_STATUS_LABELS[status],
            )
        )

    priority = {
        "available": 0,
        "not_started": 1,
        "used": 2,
        "expired": 3,
        "target_cancelled": 4,
        "source_unavailable": 5,
        "cancelled": 6,
    }
    rows.sort(
        key=lambda row: (
            priority[row.status],
            row.entitlement.valid_until,
            str(row.entitlement.id),
        )
    )
    return tuple(rows)


def student_account_snapshot(
    *,
    student_id: UUID,
    as_of: date,
    now: datetime,
) -> StudentAccountSnapshot:
    return StudentAccountSnapshot(
        subscriptions=_subscription_views(
            student_id=student_id,
            as_of=as_of,
        ),
        one_time_rights=_one_time_rights(
            student_id=student_id,
            now=now,
        ),
        makeup_rights=_makeup_rights(
            student_id=student_id,
            as_of=as_of,
        ),
    )


def student_attendance_history(*, student_id: UUID):
    active_coverages = (
        AttendanceCoverage.objects.filter(reversed_at__isnull=True)
        .select_related(
            "subscription_allowance",
            "subscription_allowance__subscription",
            "one_time_entitlement",
            "makeup_entitlement",
        )
        .order_by("created_at", "id")
    )
    return (
        Attendance.objects.filter(student_id=student_id)
        .select_related(
            "lesson",
            "lesson__lesson_type",
            "lesson__coach",
            "lesson__venue",
        )
        .prefetch_related(
            Prefetch(
                "coverages",
                queryset=active_coverages,
                to_attr="account_active_coverages",
            )
        )
        .order_by("-lesson__starts_at", "-id")
    )
