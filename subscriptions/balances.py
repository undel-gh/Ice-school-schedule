from __future__ import annotations

from datetime import date
from uuid import UUID

from django.db.models import Sum

from .models import (
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
)


def ledger_balance(allowance_id: UUID) -> int:
    value = SubscriptionLedgerEntry.objects.filter(
        allowance_id=allowance_id
    ).aggregate(balance=Sum("delta"))["balance"]
    return int(value or 0)


def locked_allowance_balance(
    allowance_id: UUID,
) -> tuple[SubscriptionAllowance, int]:
    allowance = (
        SubscriptionAllowance.objects.select_for_update()
        .select_related("subscription")
        .get(pk=allowance_id)
    )
    return allowance, ledger_balance(allowance.id)


def locked_eligible_source_allowance(
    *,
    student_id: UUID,
    category: str,
    source_date: date,
) -> tuple[SubscriptionAllowance, Subscription, int] | None:
    candidate_ids = list(
        SubscriptionAllowance.objects.filter(
            subscription__student_id=student_id,
            category=category,
            subscription__cancelled_at__isnull=True,
            subscription__valid_from__lte=source_date,
            subscription__valid_until__gte=source_date,
        )
        .order_by(
            "subscription__valid_until",
            "subscription__valid_from",
            "subscription__created_at",
            "id",
        )
        .values_list("id", flat=True)
    )

    for allowance_id in candidate_ids:
        allowance, balance = locked_allowance_balance(allowance_id)
        subscription = allowance.subscription

        if subscription.cancelled_at is not None:
            continue
        if subscription.student_id != student_id:
            continue
        if allowance.category != category:
            continue
        if not (
            subscription.valid_from
            <= source_date
            <= subscription.valid_until
        ):
            continue
        if balance <= 0:
            continue

        return allowance, subscription, balance

    return None
