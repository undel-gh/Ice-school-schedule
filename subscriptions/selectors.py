from __future__ import annotations

from uuid import UUID

from django.db.models import Sum

from .models import SubscriptionAllowance, SubscriptionLedgerEntry


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
