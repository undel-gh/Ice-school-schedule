from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, Sum

from scheduling.models import Lesson

from .models import (
    AttendanceCoverage,
    Subscription,
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
)


class SubscriptionState:
    UPCOMING = "upcoming"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AllowanceState:
    AVAILABLE = "available"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class EligibleAllowance:
    allowance: SubscriptionAllowance
    balance: int


def allowance_balance(allowance_id: UUID) -> int:
    """Return the authoritative ledger balance for one allowance."""
    allowance = SubscriptionAllowance.objects.only("id").get(pk=allowance_id)
    value = SubscriptionLedgerEntry.objects.filter(allowance=allowance).aggregate(
        balance=Sum("delta")
    )["balance"]
    return int(value or 0)


def subscription_balances(subscription_id: UUID) -> dict[str, int]:
    """Return independent balances keyed by subscription category."""
    allowances = SubscriptionAllowance.objects.filter(
        subscription_id=subscription_id
    ).order_by("category")
    return {
        allowance.category: allowance_balance(allowance.id)
        for allowance in allowances
    }


def get_eligible_allowances(
    *,
    student_id: UUID,
    category: str,
    lesson_date: date,
) -> tuple[EligibleAllowance, ...]:
    """
    Return ordinary monthly allowances eligible on lesson_date.

    This selector is read-only. Writers must still lock the selected allowance
    and recalculate its balance inside their transaction.
    """
    allowances = (
        SubscriptionAllowance.objects.filter(
            subscription__student_id=student_id,
            category=category,
            subscription__cancelled_at__isnull=True,
            subscription__valid_from__lte=lesson_date,
            subscription__valid_until__gte=lesson_date,
        )
        .select_related("subscription")
        .annotate(balance=Sum("ledger_entries__delta"))
        .filter(balance__gt=0)
        .order_by(
            "subscription__valid_until",
            "subscription__valid_from",
            "subscription__created_at",
            "id",
        )
    )
    return tuple(
        EligibleAllowance(
            allowance=allowance,
            balance=int(allowance.balance),
        )
        for allowance in allowances
    )


def get_available_one_time_entitlements(
    *,
    student_id: UUID,
    lesson_id: UUID,
    category: str,
) -> tuple[OneTimeEntitlement, ...]:
    """Return unused one-time entitlements bound to the exact lesson."""
    active_usage = AttendanceCoverage.objects.filter(
        one_time_entitlement_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    entitlements = (
        OneTimeEntitlement.objects.filter(
            student_id=student_id,
            lesson_id=lesson_id,
            category=category,
            cancelled_at__isnull=True,
        )
        .annotate(is_used=Exists(active_usage))
        .filter(is_used=False)
        .order_by("created_at", "id")
    )
    return tuple(entitlements)


def get_available_makeups(
    *,
    student_id: UUID,
    lesson_id: UUID,
    category: str,
    lesson_date: date,
) -> tuple[MakeupEntitlement, ...]:
    """
    Return currently usable make-up entitlements in coverage priority order.

    Target-specific entitlements come first, then generic entitlements by
    earliest expiry. Source allowance balance is checked read-only here and
    must be rechecked under row lock by writers.
    """
    lesson = Lesson.objects.only("id").get(pk=lesson_id)
    active_usage = AttendanceCoverage.objects.filter(
        makeup_entitlement_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    candidates = list(
        MakeupEntitlement.objects.filter(
            student_id=student_id,
            category=category,
            cancelled_at__isnull=True,
            valid_from__lte=lesson_date,
            valid_until__gte=lesson_date,
            source_subscription_allowance__subscription__student_id=student_id,
            source_subscription_allowance__subscription__cancelled_at__isnull=True,
        )
        .filter(Q(target_lesson__isnull=True) | Q(target_lesson=lesson))
        .annotate(is_used=Exists(active_usage))
        .filter(is_used=False)
        .select_related(
            "source_subscription_allowance",
            "source_subscription_allowance__subscription",
            "target_lesson",
        )
    )

    allowance_ids = {
        makeup.source_subscription_allowance_id
        for makeup in candidates
    }
    balance_rows = (
        SubscriptionLedgerEntry.objects.filter(
            allowance_id__in=allowance_ids
        )
        .values("allowance_id")
        .annotate(balance=Sum("delta"))
    )
    balances = {
        row["allowance_id"]: int(row["balance"] or 0)
        for row in balance_rows
    }
    usable = [
        makeup
        for makeup in candidates
        if balances.get(makeup.source_subscription_allowance_id, 0) > 0
    ]
    usable.sort(
        key=lambda makeup: (
            0 if makeup.target_lesson_id == lesson_id else 1,
            makeup.valid_until,
            makeup.created_at,
            str(makeup.id),
        )
    )
    return tuple(usable)



def subscription_state(
    *,
    subscription: Subscription,
    as_of: date,
) -> str:
    if subscription.cancelled_at is not None:
        return SubscriptionState.CANCELLED
    if as_of < subscription.valid_from:
        return SubscriptionState.UPCOMING
    if as_of > subscription.valid_until:
        return SubscriptionState.EXPIRED
    return SubscriptionState.ACTIVE


def allowance_state(
    *,
    allowance: SubscriptionAllowance,
    as_of: date,
) -> str:
    if subscription_state(
        subscription=allowance.subscription,
        as_of=as_of,
    ) != SubscriptionState.ACTIVE:
        return AllowanceState.EXHAUSTED
    return (
        AllowanceState.AVAILABLE
        if allowance_balance(allowance.id) > 0
        else AllowanceState.EXHAUSTED
    )
