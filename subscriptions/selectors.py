from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Exists, F, OuterRef, Prefetch, Q, Sum

from scheduling.models import Lesson

from .balances import ledger_balance
from .models import (
    AbsenceCompensationActionGrant,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
    AttendanceCoverage,
    GroupPlaceHold,
    Subscription,
    SubscriptionPeriod,
    SubscriptionPeriodScheme,
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
)


@dataclass(frozen=True, slots=True)
class ManagerAllowanceReport:
    category: str
    granted_visits: int
    consumed_visits: int
    direct_visits: int
    makeup_visits: int
    remaining_visits: int
    makeup_total: int
    makeup_available: int
    makeup_used: int
    makeup_expired_unused: int
    makeup_cancelled: int


@dataclass(frozen=True, slots=True)
class ManagerSubscriptionDetail:
    row: "ManagerSubscriptionReportRow"
    ledger_entries: tuple[SubscriptionLedgerEntry, ...]
    makeups: tuple[MakeupEntitlement, ...]


@dataclass(frozen=True, slots=True)
class ManagerSubscriptionReportRow:
    subscription: Subscription
    subscription_state: str
    period: SubscriptionPeriod | None
    allowances: tuple[ManagerAllowanceReport, ...]
    place_holds: tuple[GroupPlaceHold, ...]


class SubscriptionState:
    PENDING = "pending"
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




@dataclass(frozen=True, slots=True)
class ResolvedCompensationAction:
    policy: AbsenceCompensationPolicy
    action: AbsenceCompensationPolicyAction
    window: AbsenceCompensationPolicyWindow | None
    requirement: str
    target_from: date | None
    target_until: date | None


@dataclass(frozen=True, slots=True)
class NextStudentPeriodResolution:
    subscription: Subscription
    starts_on: date | None
    ends_on: date | None
    pending_activation: bool


def get_applicable_absence_policy(
    *,
    absence_reason: str,
    source_date: date,
    policy_code: str | None = None,
) -> AbsenceCompensationPolicy | None:
    """
    Resolve exactly one active policy version for an absence on source_date.

    Multiple matching rows are treated as configuration error rather than
    silently selecting one by creation order.
    """
    policies = AbsenceCompensationPolicy.objects.filter(
        absence_reason=absence_reason,
        is_active=True,
        effective_from__lte=source_date,
    ).filter(
        Q(effective_until__isnull=True)
        | Q(effective_until__gte=source_date)
    )
    if policy_code is not None:
        policies = policies.filter(code=policy_code)

    matches = tuple(
        policies.order_by("code", "-version", "id")
    )
    if not matches:
        return None
    if len(matches) > 1:
        raise ValidationError(
            {
                "policy": (
                    "Multiple active absence compensation policies match "
                    f"{absence_reason!r} on {source_date.isoformat()}."
                )
            }
        )
    return matches[0]


def resolve_compensation_actions(
    *,
    policy: AbsenceCompensationPolicy,
    source_date: date,
) -> tuple[ResolvedCompensationAction, ...]:
    """
    Resolve active actions plus the highest-priority matching seasonal window.

    A seasonal window overrides target dates and, when provided, the
    additional requirement for that action.
    """
    actions = (
        AbsenceCompensationPolicyAction.objects.filter(
            policy=policy,
            is_active=True,
        )
        .order_by("priority", "action_type", "id")
    )

    resolved = []
    for action in actions:
        windows = tuple(
            AbsenceCompensationPolicyWindow.objects.filter(
                policy_action=action,
                is_active=True,
                source_from__lte=source_date,
                source_until__gte=source_date,
            ).order_by("priority", "source_from", "id")
        )
        if len(windows) > 1 and windows[0].priority == windows[1].priority:
            raise ValidationError(
                {
                    "policy": (
                        "Multiple absence compensation windows with the same "
                        f"priority match action {action.id} on "
                        f"{source_date.isoformat()}."
                    )
                }
            )

        window = windows[0] if windows else None
        if (
            action.target_period_rule
            == AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
            and window is None
        ):
            continue
        requirement = (
            window.requirement_override
            if window is not None and window.requirement_override
            else action.requirement
        )
        resolved.append(
            ResolvedCompensationAction(
                policy=policy,
                action=action,
                window=window,
                requirement=requirement,
                target_from=window.target_from if window else None,
                target_until=window.target_until if window else None,
            )
        )
    return tuple(resolved)


def resolve_next_student_period(
    *,
    source_subscription: Subscription,
    category: str,
) -> NextStudentPeriodResolution | None:
    """
    Resolve the student's immediate next non-cancelled subscription period.

    Dated subscriptions are ordered by valid_from. A pending rolling
    subscription participates by billing_period.reference_date because its
    actual dates are unknown until the first covered lesson.
    """
    if source_subscription.valid_until is None:
        raise ValidationError(
            {
                "source_subscription": (
                    "Source subscription has no resolved end date."
                )
            }
        )

    source_end = source_subscription.valid_until
    base = Subscription.objects.filter(
        student_id=source_subscription.student_id,
        cancelled_at__isnull=True,
        allowances__category=category,
    ).exclude(pk=source_subscription.pk)

    dated = tuple(
        base.filter(
            valid_from__isnull=False,
            valid_until__isnull=False,
            valid_from__gt=source_end,
        ).order_by("valid_from", "created_at", "id")
    )
    pending_rolling = tuple(
        base.filter(
            valid_from__isnull=True,
            valid_until__isnull=True,
            billing_period__state=SubscriptionPeriod.State.PENDING,
            billing_period__mode_snapshot=(
                SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
            ),
            billing_period__reference_date__gt=source_end,
        )
        .select_related("billing_period")
        .order_by(
            "billing_period__reference_date",
            "created_at",
            "id",
        )
    )

    candidates: list[tuple[date, NextStudentPeriodResolution]] = []
    for subscription in dated:
        candidates.append(
            (
                subscription.valid_from,
                NextStudentPeriodResolution(
                    subscription=subscription,
                    starts_on=subscription.valid_from,
                    ends_on=subscription.valid_until,
                    pending_activation=False,
                ),
            )
        )
    for subscription in pending_rolling:
        candidates.append(
            (
                subscription.billing_period.reference_date,
                NextStudentPeriodResolution(
                    subscription=subscription,
                    starts_on=None,
                    ends_on=None,
                    pending_activation=True,
                ),
            )
        )

    if not candidates:
        return None

    earliest_date = min(item[0] for item in candidates)
    earliest = [
        resolution
        for ordering_date, resolution in candidates
        if ordering_date == earliest_date
    ]
    if len(earliest) > 1:
        raise ValidationError(
            {
                "target_subscription": (
                    "Multiple subscriptions match the next student period "
                    f"starting from {earliest_date.isoformat()}. Resolve the "
                    "duplicate/overlap before issuing compensation."
                )
            }
        )
    return earliest[0]


def allowance_balance(allowance_id: UUID) -> int:
    """Return the authoritative ledger balance for one allowance."""
    SubscriptionAllowance.objects.only("id").get(pk=allowance_id)
    return ledger_balance(allowance_id)


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



def usable_makeups_for_subscription(
    *,
    subscription_id: UUID,
    as_of: date,
) -> tuple[MakeupEntitlement, ...]:
    """
    Return make-ups funded by a Subscription that can still be consumed.

    Used make-ups (active coverage) and expired/cancelled make-ups are
    historical records and must not block Subscription cancellation.
    """
    active_usage = AttendanceCoverage.objects.filter(
        makeup_entitlement_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    return tuple(
        MakeupEntitlement.objects.filter(
            source_subscription_allowance__subscription_id=subscription_id,
            cancelled_at__isnull=True,
            valid_until__gte=as_of,
        )
        .annotate(is_used=Exists(active_usage))
        .filter(is_used=False)
        .order_by("valid_until", "created_at", "id")
    )


def get_reversed_paid_makeups(
    *,
    refund_required: bool | None = None,
) -> tuple[AbsenceCompensationActionGrant, ...]:
    """Return paid grants reversed after payment confirmation."""
    grants = AbsenceCompensationActionGrant.objects.filter(
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        fee_confirmed_at__isnull=False,
        reversed_at__isnull=False,
    )
    if refund_required is not None:
        grants = grants.filter(refund_required=refund_required)
    return tuple(
        grants.select_related(
            "case",
            "case__student",
            "target_subscription",
            "makeup_entitlement",
        ).order_by("-reversed_at", "id")
    )


def subscription_state(
    *,
    subscription: Subscription,
    as_of: date,
) -> str:
    if subscription.cancelled_at is not None:
        return SubscriptionState.CANCELLED
    if subscription.valid_from is None or subscription.valid_until is None:
        return SubscriptionState.PENDING
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



def manager_subscription_report(
    *,
    as_of: date,
    student_id: UUID | None = None,
    subscription_id: UUID | None = None,
    from_date: date | None = None,
    until_date: date | None = None,
) -> tuple[ManagerSubscriptionReportRow, ...]:
    """
    Read model for the manager's subscription report.

    Related report data is batch-loaded so report query count stays bounded
    as the number of subscriptions grows.
    """
    active_makeup_usage = AttendanceCoverage.objects.filter(
        makeup_entitlement_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    allowance_queryset = (
        SubscriptionAllowance.objects.annotate(
            report_balance=Sum("ledger_entries__delta"),
        )
        .prefetch_related(
            Prefetch(
                "coverages",
                queryset=AttendanceCoverage.objects.filter(
                    reversed_at__isnull=True,
                ).only(
                    "id",
                    "subscription_allowance_id",
                    "makeup_entitlement_id",
                ),
                to_attr="report_active_coverages",
            ),
            Prefetch(
                "makeup_entitlements",
                queryset=MakeupEntitlement.objects.annotate(
                    report_is_used=Exists(active_makeup_usage),
                ).only(
                    "id",
                    "source_subscription_allowance_id",
                    "valid_from",
                    "valid_until",
                    "cancelled_at",
                ),
                to_attr="report_makeups",
            ),
        )
        .order_by("category", "id")
    )
    hold_queryset = (
        GroupPlaceHold.objects.select_related(
            "group",
            "period_scheme",
        )
        .order_by("period_from", "group__name", "id")
    )
    subscriptions = (
        Subscription.objects.select_related(
            "student",
            "plan",
            "billing_period",
        )
        .prefetch_related(
            Prefetch(
                "allowances",
                queryset=allowance_queryset,
                to_attr="report_allowances",
            ),
            Prefetch(
                "student__group_place_holds",
                queryset=hold_queryset,
                to_attr="report_place_holds",
            ),
        )
        .order_by(
            F("valid_from").desc(nulls_first=True),
            "student__display_name",
            "id",
        )
    )
    if student_id is not None:
        subscriptions = subscriptions.filter(student_id=student_id)
    if subscription_id is not None:
        subscriptions = subscriptions.filter(id=subscription_id)
    if from_date is not None:
        subscriptions = subscriptions.filter(
            Q(valid_until__gte=from_date)
            | Q(
                valid_from__isnull=True,
                created_at__date__gte=from_date,
            )
        )
    if until_date is not None:
        subscriptions = subscriptions.filter(
            Q(valid_from__lte=until_date)
            | Q(
                valid_from__isnull=True,
                created_at__date__lte=until_date,
            )
        )

    rows: list[ManagerSubscriptionReportRow] = []
    for subscription in subscriptions:
        try:
            period = subscription.billing_period
        except SubscriptionPeriod.DoesNotExist:
            period = None

        allowance_rows: list[ManagerAllowanceReport] = []
        for allowance in subscription.report_allowances:
            coverages = allowance.report_active_coverages
            direct_visits = sum(
                coverage.makeup_entitlement_id is None
                for coverage in coverages
            )
            makeup_visits = len(coverages) - direct_visits

            makeups = allowance.report_makeups
            makeup_used = sum(makeup.report_is_used for makeup in makeups)
            makeup_available = sum(
                makeup.cancelled_at is None
                and makeup.valid_from <= as_of <= makeup.valid_until
                and not makeup.report_is_used
                for makeup in makeups
            )
            makeup_expired_unused = sum(
                makeup.cancelled_at is None
                and makeup.valid_until < as_of
                and not makeup.report_is_used
                for makeup in makeups
            )
            makeup_cancelled = sum(
                makeup.cancelled_at is not None
                for makeup in makeups
            )

            allowance_rows.append(
                ManagerAllowanceReport(
                    category=allowance.category,
                    granted_visits=allowance.visit_limit_snapshot,
                    consumed_visits=direct_visits + makeup_visits,
                    direct_visits=direct_visits,
                    makeup_visits=makeup_visits,
                    remaining_visits=int(allowance.report_balance or 0),
                    makeup_total=len(makeups),
                    makeup_available=makeup_available,
                    makeup_used=makeup_used,
                    makeup_expired_unused=makeup_expired_unused,
                    makeup_cancelled=makeup_cancelled,
                )
            )

        if (
            subscription.valid_from is not None
            and subscription.valid_until is not None
        ):
            place_holds = tuple(
                hold
                for hold in subscription.student.report_place_holds
                if (
                    hold.period_until >= subscription.valid_from
                    and hold.period_from <= subscription.valid_until
                )
            )
        else:
            place_holds = ()

        rows.append(
            ManagerSubscriptionReportRow(
                subscription=subscription,
                subscription_state=subscription_state(
                    subscription=subscription,
                    as_of=as_of,
                ),
                period=period,
                allowances=tuple(allowance_rows),
                place_holds=place_holds,
            )
        )
    return tuple(rows)


def manager_subscription_detail(
    *,
    subscription_id: UUID,
    as_of: date,
) -> ManagerSubscriptionDetail:
    rows = manager_subscription_report(
        as_of=as_of,
        subscription_id=subscription_id,
    )
    if not rows:
        raise Subscription.DoesNotExist
    row = rows[0]
    subscription = row.subscription
    ledger_entries = tuple(
        SubscriptionLedgerEntry.objects.filter(
            allowance__subscription=subscription,
        )
        .select_related(
            "allowance",
            "coverage",
        )
        .order_by("-created_at", "-id")
    )
    makeups = tuple(
        MakeupEntitlement.objects.filter(
            source_subscription_allowance__subscription=subscription,
        )
        .select_related(
            "source_lesson",
            "target_lesson",
        )
        .order_by("-created_at", "-id")
    )
    return ManagerSubscriptionDetail(
        row=row,
        ledger_entries=ledger_entries,
        makeups=makeups,
    )
