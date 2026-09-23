from __future__ import annotations

from datetime import date
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Student
from attendance.models import Attendance
from audit.models import AuditEvent

from .models import (
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)

User = get_user_model()


def _locked_allowance_balance(
    allowance_id: UUID,
) -> tuple[SubscriptionAllowance, int]:
    allowance = SubscriptionAllowance.objects.select_for_update().get(pk=allowance_id)
    balance = SubscriptionLedgerEntry.objects.filter(
        allowance_id=allowance.id
    ).aggregate(balance=Sum("delta"))["balance"]
    return allowance, int(balance or 0)


def _lesson_date(attendance: Attendance) -> date:
    starts_at = attendance.lesson.starts_at
    if timezone.is_aware(starts_at):
        return timezone.localtime(starts_at).date()
    return starts_at.date()


def _audit(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    actor: User | None,
    payload: dict | None = None,
) -> None:
    AuditEvent.objects.create(
        event_type=event_type,
        actor=actor,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload or {},
    )


@transaction.atomic
def issue_subscription(
    *,
    student_id: UUID,
    plan_id: UUID,
    valid_from: date,
    valid_until: date,
    actor: User | None,
) -> Subscription:
    if valid_until < valid_from:
        raise ValidationError(
            {"valid_until": "valid_until must be on or after valid_from."}
        )

    student = Student.objects.get(pk=student_id)
    plan = SubscriptionPlan.objects.select_for_update().get(pk=plan_id)
    if not plan.is_active:
        raise ValidationError(
            {"plan": "Inactive subscription plans cannot be issued."}
        )

    plan_allowances = list(
        SubscriptionPlanAllowance.objects.select_for_update()
        .filter(plan_id=plan.id)
        .order_by("category", "id")
    )
    if not plan_allowances:
        raise ValidationError({"plan": "Subscription plan has no allowances."})

    subscription = Subscription.objects.create(
        student=student,
        plan=plan,
        plan_code_snapshot=plan.code,
        plan_name_snapshot=plan.name,
        valid_from=valid_from,
        valid_until=valid_until,
        created_by=actor,
    )

    issued: dict[str, int] = {}
    for source in plan_allowances:
        allowance = SubscriptionAllowance.objects.create(
            subscription=subscription,
            category=source.category,
            visit_limit_snapshot=source.visit_limit,
        )
        SubscriptionLedgerEntry.objects.create(
            allowance=allowance,
            entry_type=SubscriptionLedgerEntry.EntryType.GRANT,
            delta=source.visit_limit,
            reason="Initial subscription grant",
            created_by=actor,
        )
        issued[source.category] = source.visit_limit

    _audit(
        event_type="SubscriptionIssued",
        aggregate_type="Subscription",
        aggregate_id=subscription.id,
        actor=actor,
        payload={
            "student_id": str(student.id),
            "plan_id": str(plan.id),
            "plan_code": subscription.plan_code_snapshot,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
            "allowances": issued,
        },
    )
    return subscription


def _active_coverage_for_attendance(
    attendance_id: UUID,
) -> AttendanceCoverage | None:
    return (
        AttendanceCoverage.objects.filter(
            attendance_id=attendance_id,
            reversed_at__isnull=True,
        )
        .select_related(
            "subscription_allowance",
            "one_time_entitlement",
            "makeup_entitlement",
        )
        .first()
    )


def _try_one_time_coverage(
    *,
    attendance: Attendance,
    category: str,
    actor: User | None,
) -> AttendanceCoverage | None:
    candidate_ids = list(
        OneTimeEntitlement.objects.filter(
            student_id=attendance.student_id,
            lesson_id=attendance.lesson_id,
            category=category,
            cancelled_at__isnull=True,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )

    for entitlement_id in candidate_ids:
        entitlement = (
            OneTimeEntitlement.objects.select_for_update().get(pk=entitlement_id)
        )
        if entitlement.cancelled_at is not None:
            continue
        if AttendanceCoverage.objects.filter(
            one_time_entitlement_id=entitlement.id,
            reversed_at__isnull=True,
        ).exists():
            continue

        coverage = AttendanceCoverage.objects.create(
            attendance=attendance,
            one_time_entitlement=entitlement,
            created_by=actor,
        )
        _audit(
            event_type="AttendanceCoverageAssigned",
            aggregate_type="AttendanceCoverage",
            aggregate_id=coverage.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "source": "one_time",
                "one_time_entitlement_id": str(entitlement.id),
                "category": category,
            },
        )
        return coverage

    return None


def _validate_makeup_source(
    *,
    makeup: MakeupEntitlement,
    allowance: SubscriptionAllowance,
    attendance: Attendance,
    category: str,
) -> Subscription:
    subscription = Subscription.objects.get(pk=allowance.subscription_id)

    if makeup.source_subscription_allowance_id != allowance.id:
        raise ValidationError(
            "Makeup entitlement source allowance changed unexpectedly."
        )
    if (
        makeup.student_id != attendance.student_id
        or subscription.student_id != attendance.student_id
    ):
        raise ValidationError(
            "Makeup entitlement source allowance belongs to another student."
        )
    if makeup.category != category or allowance.category != category:
        raise ValidationError(
            "Makeup entitlement category does not match the lesson category."
        )

    return subscription


def _try_makeup_coverage(
    *,
    attendance: Attendance,
    category: str,
    lesson_date: date,
    actor: User | None,
) -> AttendanceCoverage | None:
    base = MakeupEntitlement.objects.filter(
        student_id=attendance.student_id,
        category=category,
        cancelled_at__isnull=True,
        valid_from__lte=lesson_date,
        valid_until__gte=lesson_date,
    )

    candidate_ids = list(
        base.filter(target_lesson_id=attendance.lesson_id)
        .order_by("valid_until", "created_at", "id")
        .values_list("id", flat=True)
    )
    candidate_ids.extend(
        base.filter(target_lesson__isnull=True)
        .order_by("valid_until", "created_at", "id")
        .values_list("id", flat=True)
    )

    for makeup_id in candidate_ids:
        makeup = MakeupEntitlement.objects.select_for_update().get(pk=makeup_id)

        if makeup.cancelled_at is not None:
            continue
        if not (makeup.valid_from <= lesson_date <= makeup.valid_until):
            continue
        if makeup.target_lesson_id not in (None, attendance.lesson_id):
            continue
        if AttendanceCoverage.objects.filter(
            makeup_entitlement_id=makeup.id,
            reversed_at__isnull=True,
        ).exists():
            continue

        allowance, balance = _locked_allowance_balance(
            makeup.source_subscription_allowance_id
        )
        source_subscription = _validate_makeup_source(
            makeup=makeup,
            allowance=allowance,
            attendance=attendance,
            category=category,
        )
        if source_subscription.cancelled_at is not None or balance <= 0:
            continue

        coverage = AttendanceCoverage.objects.create(
            attendance=attendance,
            subscription_allowance=allowance,
            makeup_entitlement=makeup,
            created_by=actor,
        )
        SubscriptionLedgerEntry.objects.create(
            allowance=allowance,
            coverage=coverage,
            entry_type=SubscriptionLedgerEntry.EntryType.CONSUME,
            delta=-1,
            reason="Attendance covered by make-up entitlement",
            created_by=actor,
        )
        _audit(
            event_type="AttendanceCoverageAssigned",
            aggregate_type="AttendanceCoverage",
            aggregate_id=coverage.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "source": "makeup",
                "allowance_id": str(allowance.id),
                "makeup_entitlement_id": str(makeup.id),
                "category": category,
            },
        )
        return coverage

    return None


def _try_ordinary_allowance_coverage(
    *,
    attendance: Attendance,
    category: str,
    lesson_date: date,
    actor: User | None,
) -> AttendanceCoverage | None:
    candidate_ids = list(
        SubscriptionAllowance.objects.filter(
            subscription__student_id=attendance.student_id,
            category=category,
            subscription__cancelled_at__isnull=True,
            subscription__valid_from__lte=lesson_date,
            subscription__valid_until__gte=lesson_date,
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
        allowance, balance = _locked_allowance_balance(allowance_id)

        subscription = Subscription.objects.get(
            pk=allowance.subscription_id
        )
        if subscription.student_id != attendance.student_id:
            continue
        if subscription.cancelled_at is not None:
            continue
        if allowance.category != category:
            continue
        if not (
            subscription.valid_from
            <= lesson_date
            <= subscription.valid_until
        ):
            continue
        if balance <= 0:
            continue

        coverage = AttendanceCoverage.objects.create(
            attendance=attendance,
            subscription_allowance=allowance,
            created_by=actor,
        )
        SubscriptionLedgerEntry.objects.create(
            allowance=allowance,
            coverage=coverage,
            entry_type=SubscriptionLedgerEntry.EntryType.CONSUME,
            delta=-1,
            reason="Attendance covered by subscription allowance",
            created_by=actor,
        )
        _audit(
            event_type="AttendanceCoverageAssigned",
            aggregate_type="AttendanceCoverage",
            aggregate_id=coverage.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "source": "subscription",
                "allowance_id": str(allowance.id),
                "category": category,
            },
        )
        return coverage

    return None


@transaction.atomic
def assign_attendance_coverage(
    *,
    attendance_id: UUID,
    actor: User | None = None,
) -> AttendanceCoverage | None:
    attendance = (
        Attendance.objects.select_for_update()
        .select_related("lesson__lesson_type", "student")
        .get(pk=attendance_id)
    )

    if attendance.status != Attendance.Status.PRESENT:
        raise ValidationError(
            {
                "attendance": (
                    "Coverage can only be assigned to PRESENT attendance."
                )
            }
        )

    existing = _active_coverage_for_attendance(attendance.id)
    if existing is not None:
        return existing

    category = attendance.lesson.lesson_type.subscription_category
    lesson_date = _lesson_date(attendance)

    coverage = _try_one_time_coverage(
        attendance=attendance,
        category=category,
        actor=actor,
    )
    if coverage is not None:
        return coverage

    coverage = _try_makeup_coverage(
        attendance=attendance,
        category=category,
        lesson_date=lesson_date,
        actor=actor,
    )
    if coverage is not None:
        return coverage

    return _try_ordinary_allowance_coverage(
        attendance=attendance,
        category=category,
        lesson_date=lesson_date,
        actor=actor,
    )


@transaction.atomic
def reverse_attendance_coverage(
    *,
    coverage_id: UUID,
    actor: User | None = None,
) -> AttendanceCoverage:
    coverage = AttendanceCoverage.objects.select_for_update().get(
        pk=coverage_id
    )
    if coverage.reversed_at is not None:
        return coverage

    if coverage.subscription_allowance_id is not None:
        allowance, _ = _locked_allowance_balance(
            coverage.subscription_allowance_id
        )
        SubscriptionLedgerEntry.objects.create(
            allowance=allowance,
            coverage=coverage,
            entry_type=SubscriptionLedgerEntry.EntryType.RESTORE,
            delta=1,
            reason="Attendance coverage reversed",
            created_by=actor,
        )

    coverage.reversed_at = timezone.now()
    coverage.reversed_by = actor
    coverage.save(update_fields=["reversed_at", "reversed_by"])

    _audit(
        event_type="AttendanceCoverageReversed",
        aggregate_type="AttendanceCoverage",
        aggregate_id=coverage.id,
        actor=actor,
        payload={"attendance_id": str(coverage.attendance_id)},
    )
    return coverage


@transaction.atomic
def adjust_allowance(
    *,
    allowance_id: UUID,
    delta: int,
    reason: str,
    actor: User,
) -> SubscriptionLedgerEntry:
    if delta == 0:
        raise ValidationError(
            {"delta": "Adjustment delta must be non-zero."}
        )
    if not reason.strip():
        raise ValidationError(
            {"reason": "Adjustment reason is required."}
        )

    allowance, balance = _locked_allowance_balance(allowance_id)
    if balance + delta < 0:
        raise ValidationError(
            {
                "delta": (
                    "Adjustment would make allowance balance negative."
                )
            }
        )

    entry = SubscriptionLedgerEntry.objects.create(
        allowance=allowance,
        entry_type=SubscriptionLedgerEntry.EntryType.ADJUSTMENT,
        delta=delta,
        reason=reason.strip(),
        created_by=actor,
    )
    _audit(
        event_type="SubscriptionAllowanceAdjusted",
        aggregate_type="SubscriptionAllowance",
        aggregate_id=allowance.id,
        actor=actor,
        payload={
            "delta": delta,
            "reason": reason.strip(),
            "balance_before": balance,
            "balance_after": balance + delta,
        },
    )
    return entry


@transaction.atomic
def cancel_subscription(
    *,
    subscription_id: UUID,
    actor: User,
    at=None,
) -> Subscription:
    subscription = Subscription.objects.select_for_update().get(
        pk=subscription_id
    )
    if subscription.cancelled_at is not None:
        return subscription

    list(
        SubscriptionAllowance.objects.select_for_update()
        .filter(subscription_id=subscription.id)
        .order_by("id")
        .values_list("id", flat=True)
    )

    cancelled_at = at or timezone.now()
    subscription.cancelled_at = cancelled_at
    subscription.cancelled_by = actor
    subscription.save(
        update_fields=["cancelled_at", "cancelled_by"]
    )

    _audit(
        event_type="SubscriptionCancelled",
        aggregate_type="Subscription",
        aggregate_id=subscription.id,
        actor=actor,
        payload={"cancelled_at": cancelled_at.isoformat()},
    )
    return subscription
