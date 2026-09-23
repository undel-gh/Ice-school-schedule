from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from accounts.models import Student
from attendance.models import Attendance
from audit.models import AuditEvent
from core.time import school_date
from scheduling.models import Lesson

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


def _audit(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    actor: User | None,
    payload: dict | None = None,
    correlation_id: UUID | None = None,
) -> None:
    values = {
        "event_type": event_type,
        "actor": actor,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "payload": payload or {},
    }
    if correlation_id is not None:
        values["correlation_id"] = correlation_id
    AuditEvent.objects.create(**values)


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
    correlation_id: UUID,
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
            correlation_id=correlation_id,
        )
        _audit(
            event_type="OneTimeEntitlementUsed",
            aggregate_type="OneTimeEntitlement",
            aggregate_id=entitlement.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(coverage.id),
                "category": category,
            },
            correlation_id=correlation_id,
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
    correlation_id: UUID,
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
            correlation_id=correlation_id,
        )
        _audit(
            event_type="SubscriptionAllowanceConsumed",
            aggregate_type="SubscriptionAllowance",
            aggregate_id=allowance.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(coverage.id),
                "allowance_id": str(allowance.id),
                "category": category,
                "delta": -1,
            },
            correlation_id=correlation_id,
        )
        remaining_balance = balance - 1
        if remaining_balance == 0:
            _audit(
                event_type="SubscriptionAllowanceExhausted",
                aggregate_type="SubscriptionAllowance",
                aggregate_id=allowance.id,
                actor=actor,
                payload={
                    "attendance_id": str(attendance.id),
                    "coverage_id": str(coverage.id),
                    "allowance_id": str(allowance.id),
                    "category": category,
                    "balance": 0,
                },
                correlation_id=correlation_id,
            )
        _audit(
            event_type="MakeupEntitlementUsed",
            aggregate_type="MakeupEntitlement",
            aggregate_id=makeup.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(coverage.id),
                "allowance_id": str(allowance.id),
            },
            correlation_id=correlation_id,
        )
        return coverage

    return None


def _try_ordinary_allowance_coverage(
    *,
    attendance: Attendance,
    category: str,
    lesson_date: date,
    actor: User | None,
    correlation_id: UUID,
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
            correlation_id=correlation_id,
        )
        _audit(
            event_type="SubscriptionAllowanceConsumed",
            aggregate_type="SubscriptionAllowance",
            aggregate_id=allowance.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(coverage.id),
                "allowance_id": str(allowance.id),
                "category": category,
                "delta": -1,
            },
            correlation_id=correlation_id,
        )
        remaining_balance = balance - 1
        if remaining_balance == 0:
            _audit(
                event_type="SubscriptionAllowanceExhausted",
                aggregate_type="SubscriptionAllowance",
                aggregate_id=allowance.id,
                actor=actor,
                payload={
                    "attendance_id": str(attendance.id),
                    "coverage_id": str(coverage.id),
                    "allowance_id": str(allowance.id),
                    "category": category,
                    "balance": 0,
                },
                correlation_id=correlation_id,
            )
        return coverage

    return None


@transaction.atomic
def assign_attendance_coverage(
    *,
    attendance_id: UUID,
    actor: User | None = None,
    correlation_id: UUID | None = None,
) -> AttendanceCoverage | None:
    correlation_id = correlation_id or uuid4()
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
    lesson_date = school_date(attendance.lesson.starts_at)

    coverage = _try_one_time_coverage(
        attendance=attendance,
        category=category,
        actor=actor,
        correlation_id=correlation_id,
    )
    if coverage is not None:
        return coverage

    coverage = _try_makeup_coverage(
        attendance=attendance,
        category=category,
        lesson_date=lesson_date,
        actor=actor,
        correlation_id=correlation_id,
    )
    if coverage is not None:
        return coverage

    return _try_ordinary_allowance_coverage(
        attendance=attendance,
        category=category,
        lesson_date=lesson_date,
        actor=actor,
        correlation_id=correlation_id,
    )


@transaction.atomic
def reverse_attendance_coverage(
    *,
    coverage_id: UUID,
    actor: User | None = None,
    correlation_id: UUID | None = None,
) -> AttendanceCoverage:
    correlation_id = correlation_id or uuid4()
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
        correlation_id=correlation_id,
    )
    if coverage.subscription_allowance_id is not None:
        _audit(
            event_type="SubscriptionAllowanceRestored",
            aggregate_type="SubscriptionAllowance",
            aggregate_id=coverage.subscription_allowance_id,
            actor=actor,
            payload={
                "attendance_id": str(coverage.attendance_id),
                "coverage_id": str(coverage.id),
                "allowance_id": str(coverage.subscription_allowance_id),
                "delta": 1,
            },
            correlation_id=correlation_id,
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



def _assert_entitlement_admin(actor: User) -> None:
    if actor.is_staff or actor.is_superuser:
        return
    raise PermissionDenied(
        "Only an administrator may manage administrative entitlements."
    )


@transaction.atomic
def grant_administrative_makeup(
    *,
    source_subscription_allowance_id: UUID,
    source_lesson_id: UUID,
    valid_from: date,
    valid_until: date,
    actor: User,
    reason: str,
    target_lesson_id: UUID | None = None,
) -> MakeupEntitlement:
    _assert_entitlement_admin(actor)

    if valid_until < valid_from:
        raise ValidationError(
            {"valid_until": "valid_until must be on or after valid_from."}
        )
    if not reason.strip():
        raise ValidationError(
            {"reason": "Administrative reason is required."}
        )

    allowance, balance = _locked_allowance_balance(
        source_subscription_allowance_id
    )
    subscription = Subscription.objects.select_for_update().get(
        pk=allowance.subscription_id
    )
    if subscription.cancelled_at is not None:
        raise ValidationError(
            {"allowance": "Cancelled subscription cannot fund a make-up."}
        )
    if balance <= 0:
        raise ValidationError(
            {"allowance": "Source allowance has no remaining visits."}
        )

    source_lesson = Lesson.objects.select_related("lesson_type").get(
        pk=source_lesson_id
    )
    if (
        source_lesson.lesson_type.subscription_category
        != allowance.category
    ):
        raise ValidationError(
            {
                "source_lesson": (
                    "Source lesson category does not match source allowance."
                )
            }
        )

    target_lesson = None
    if target_lesson_id is not None:
        target_lesson = Lesson.objects.select_related("lesson_type").get(
            pk=target_lesson_id
        )
        if (
            target_lesson.lesson_type.subscription_category
            != allowance.category
        ):
            raise ValidationError(
                {
                    "target_lesson": (
                        "Target lesson category does not match source "
                        "allowance."
                    )
                }
            )
        target_date = school_date(target_lesson.starts_at)
        if not (valid_from <= target_date <= valid_until):
            raise ValidationError(
                {
                    "target_lesson": (
                        "Target lesson date must fall inside the make-up "
                        "validity period."
                    )
                }
            )

    existing = (
        MakeupEntitlement.objects.select_for_update()
        .filter(
            student_id=subscription.student_id,
            source_lesson_id=source_lesson.id,
            reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        )
        .first()
    )
    if existing is not None:
        if existing.cancelled_at is None:
            return existing
        raise ValidationError(
            {
                "source_lesson": (
                    "An administrative make-up already exists historically "
                    "for this student and source lesson."
                )
            }
        )

    entitlement = MakeupEntitlement.objects.create(
        student_id=subscription.student_id,
        source_lesson=source_lesson,
        source_subscription_allowance=allowance,
        category=allowance.category,
        reason=MakeupEntitlement.Reason.ADMINISTRATIVE,
        valid_from=valid_from,
        valid_until=valid_until,
        target_lesson=target_lesson,
        created_by=actor,
    )

    _audit(
        event_type="MakeupEntitlementGranted",
        aggregate_type="MakeupEntitlement",
        aggregate_id=entitlement.id,
        actor=actor,
        payload={
            "student_id": str(subscription.student_id),
            "source_lesson_id": str(source_lesson.id),
            "source_allowance_id": str(allowance.id),
            "category": allowance.category,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
            "target_lesson_id": (
                str(target_lesson.id) if target_lesson is not None else None
            ),
            "reason": reason.strip(),
            "entitlement_reason": entitlement.reason,
        },
    )
    return entitlement


def _coverage_matches_target(
    *,
    coverage: AttendanceCoverage,
    one_time_entitlement_id: UUID | None,
    subscription_allowance_id: UUID | None,
    makeup_entitlement_id: UUID | None,
) -> bool:
    return (
        coverage.one_time_entitlement_id == one_time_entitlement_id
        and coverage.subscription_allowance_id == subscription_allowance_id
        and coverage.makeup_entitlement_id == makeup_entitlement_id
    )


@transaction.atomic
def rebind_attendance_coverage(
    *,
    attendance_id: UUID,
    actor: User,
    one_time_entitlement_id: UUID | None = None,
    subscription_allowance_id: UUID | None = None,
    makeup_entitlement_id: UUID | None = None,
) -> AttendanceCoverage:
    correlation_id = uuid4()
    _assert_entitlement_admin(actor)

    primary_count = sum(
        value is not None
        for value in (
            one_time_entitlement_id,
            subscription_allowance_id,
        )
    )
    if primary_count != 1:
        raise ValidationError(
            "Exactly one target primary source must be supplied."
        )
    if (
        makeup_entitlement_id is not None
        and subscription_allowance_id is None
    ):
        raise ValidationError(
            "Make-up entitlement requires a subscription allowance target."
        )

    attendance_ref = (
        Attendance.objects.select_related("lesson__lesson_type")
        .get(pk=attendance_id)
    )
    lesson = (
        Lesson.objects.select_for_update()
        .select_related("lesson_type")
        .get(pk=attendance_ref.lesson_id)
    )
    if lesson.status != Lesson.Status.COMPLETED:
        raise ValidationError(
            {
                "lesson": (
                    "Attendance coverage can only be rebound while the "
                    "lesson is COMPLETED. Reopen CLOSED attendance first."
                )
            }
        )

    attendance = (
        Attendance.objects.select_for_update()
        .select_related("lesson__lesson_type")
        .get(pk=attendance_id)
    )
    if attendance.status != Attendance.Status.PRESENT:
        raise ValidationError(
            {"attendance": "Only PRESENT attendance can be rebound."}
        )

    old_coverage = (
        AttendanceCoverage.objects.select_for_update()
        .filter(
            attendance=attendance,
            reversed_at__isnull=True,
        )
        .first()
    )
    if old_coverage is None:
        raise ValidationError(
            {"attendance": "Attendance has no active coverage to rebind."}
        )

    if _coverage_matches_target(
        coverage=old_coverage,
        one_time_entitlement_id=one_time_entitlement_id,
        subscription_allowance_id=subscription_allowance_id,
        makeup_entitlement_id=makeup_entitlement_id,
    ):
        return old_coverage

    category = attendance.lesson.lesson_type.subscription_category
    lesson_date = school_date(attendance.lesson.starts_at)

    target_one_time = None
    target_makeup = None
    target_allowance_id = subscription_allowance_id

    if one_time_entitlement_id is not None:
        target_one_time = OneTimeEntitlement.objects.select_for_update().get(
            pk=one_time_entitlement_id
        )
        if target_one_time.cancelled_at is not None:
            raise ValidationError(
                {"one_time_entitlement": "Entitlement is cancelled."}
            )
        if (
            target_one_time.student_id != attendance.student_id
            or target_one_time.lesson_id != attendance.lesson_id
            or target_one_time.category != category
        ):
            raise ValidationError(
                {
                    "one_time_entitlement": (
                        "Entitlement does not match attendance student, "
                        "lesson, or category."
                    )
                }
            )
        if AttendanceCoverage.objects.filter(
            one_time_entitlement=target_one_time,
            reversed_at__isnull=True,
        ).exclude(pk=old_coverage.pk).exists():
            raise ValidationError(
                {"one_time_entitlement": "Entitlement is already in use."}
            )

    if makeup_entitlement_id is not None:
        target_makeup = MakeupEntitlement.objects.select_for_update().get(
            pk=makeup_entitlement_id
        )
        if target_makeup.cancelled_at is not None:
            raise ValidationError(
                {"makeup_entitlement": "Make-up entitlement is cancelled."}
            )
        if (
            target_makeup.student_id != attendance.student_id
            or target_makeup.category != category
            or not (
                target_makeup.valid_from
                <= lesson_date
                <= target_makeup.valid_until
            )
            or target_makeup.target_lesson_id
            not in (None, attendance.lesson_id)
        ):
            raise ValidationError(
                {
                    "makeup_entitlement": (
                        "Make-up entitlement is not valid for this "
                        "attendance."
                    )
                }
            )
        if (
            target_makeup.source_subscription_allowance_id
            != subscription_allowance_id
        ):
            raise ValidationError(
                {
                    "makeup_entitlement": (
                        "Make-up entitlement does not belong to target "
                        "allowance."
                    )
                }
            )
        if AttendanceCoverage.objects.filter(
            makeup_entitlement=target_makeup,
            reversed_at__isnull=True,
        ).exclude(pk=old_coverage.pk).exists():
            raise ValidationError(
                {"makeup_entitlement": "Make-up entitlement is already in use."}
            )

    allowance_ids = {
        value
        for value in (
            old_coverage.subscription_allowance_id,
            target_allowance_id,
        )
        if value is not None
    }
    locked_allowances = {
        allowance.id: allowance
        for allowance in SubscriptionAllowance.objects.select_for_update()
        .select_related("subscription")
        .filter(id__in=allowance_ids)
        .order_by(
            "subscription__valid_until",
            "subscription__valid_from",
            "subscription__created_at",
            "id",
        )
    }

    target_allowance = None
    if target_allowance_id is not None:
        target_allowance = locked_allowances[target_allowance_id]
        target_subscription = target_allowance.subscription
        if (
            target_subscription.student_id != attendance.student_id
            or target_allowance.category != category
            or target_subscription.cancelled_at is not None
        ):
            raise ValidationError(
                {
                    "subscription_allowance": (
                        "Allowance does not match attendance student/category "
                        "or belongs to a cancelled subscription."
                    )
                }
            )
        if target_makeup is None and not (
            target_subscription.valid_from
            <= lesson_date
            <= target_subscription.valid_until
        ):
            raise ValidationError(
                {
                    "subscription_allowance": (
                        "Ordinary allowance is not valid on lesson date."
                    )
                }
            )

    old_allowance = (
        locked_allowances.get(old_coverage.subscription_allowance_id)
        if old_coverage.subscription_allowance_id is not None
        else None
    )

    if old_allowance is not None:
        if not SubscriptionLedgerEntry.objects.filter(
            coverage=old_coverage,
            entry_type=SubscriptionLedgerEntry.EntryType.RESTORE,
        ).exists():
            SubscriptionLedgerEntry.objects.create(
                allowance=old_allowance,
                coverage=old_coverage,
                entry_type=SubscriptionLedgerEntry.EntryType.RESTORE,
                delta=1,
                reason="Attendance coverage rebound",
                created_by=actor,
            )

    old_coverage.reversed_at = timezone.now()
    old_coverage.reversed_by = actor
    old_coverage.save(update_fields=["reversed_at", "reversed_by"])

    if target_allowance is not None:
        target_balance = SubscriptionLedgerEntry.objects.filter(
            allowance=target_allowance
        ).aggregate(balance=Sum("delta"))["balance"]
        target_balance = int(target_balance or 0)
        if target_balance <= 0:
            raise ValidationError(
                {
                    "subscription_allowance": (
                        "Target allowance has no remaining visits."
                    )
                }
            )

    new_coverage = AttendanceCoverage.objects.create(
        attendance=attendance,
        subscription_allowance=target_allowance,
        one_time_entitlement=target_one_time,
        makeup_entitlement=target_makeup,
        created_by=actor,
    )

    if target_allowance is not None:
        SubscriptionLedgerEntry.objects.create(
            allowance=target_allowance,
            coverage=new_coverage,
            entry_type=SubscriptionLedgerEntry.EntryType.CONSUME,
            delta=-1,
            reason="Attendance coverage rebound",
            created_by=actor,
        )

    _audit(
        event_type="AttendanceCoverageReversed",
        aggregate_type="AttendanceCoverage",
        aggregate_id=old_coverage.id,
        actor=actor,
        payload={
            "attendance_id": str(attendance.id),
            "reason": "rebind",
        },
        correlation_id=correlation_id,
    )
    _audit(
        event_type="AttendanceCoverageAssigned",
        aggregate_type="AttendanceCoverage",
        aggregate_id=new_coverage.id,
        actor=actor,
        payload={
            "attendance_id": str(attendance.id),
            "source": (
                "one_time"
                if target_one_time is not None
                else "makeup"
                if target_makeup is not None
                else "subscription"
            ),
            "one_time_entitlement_id": (
                str(target_one_time.id)
                if target_one_time is not None
                else None
            ),
            "allowance_id": (
                str(target_allowance.id)
                if target_allowance is not None
                else None
            ),
            "makeup_entitlement_id": (
                str(target_makeup.id)
                if target_makeup is not None
                else None
            ),
            "category": category,
        },
        correlation_id=correlation_id,
    )
    _audit(
        event_type="AttendanceCoverageRebound",
        aggregate_type="AttendanceCoverage",
        aggregate_id=new_coverage.id,
        actor=actor,
        payload={
            "attendance_id": str(attendance.id),
            "old_coverage_id": str(old_coverage.id),
            "new_coverage_id": str(new_coverage.id),
        },
        correlation_id=correlation_id,
    )

    if old_allowance is not None:
        _audit(
            event_type="SubscriptionAllowanceRestored",
            aggregate_type="SubscriptionAllowance",
            aggregate_id=old_allowance.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(old_coverage.id),
                "allowance_id": str(old_allowance.id),
                "delta": 1,
                "reason": "rebind",
            },
            correlation_id=correlation_id,
        )

    if target_allowance is not None:
        _audit(
            event_type="SubscriptionAllowanceConsumed",
            aggregate_type="SubscriptionAllowance",
            aggregate_id=target_allowance.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(new_coverage.id),
                "allowance_id": str(target_allowance.id),
                "category": category,
                "delta": -1,
                "reason": "rebind",
            },
            correlation_id=correlation_id,
        )
        if target_balance == 1:
            _audit(
                event_type="SubscriptionAllowanceExhausted",
                aggregate_type="SubscriptionAllowance",
                aggregate_id=target_allowance.id,
                actor=actor,
                payload={
                    "attendance_id": str(attendance.id),
                    "coverage_id": str(new_coverage.id),
                    "allowance_id": str(target_allowance.id),
                    "category": category,
                    "balance": 0,
                    "reason": "rebind",
                },
                correlation_id=correlation_id,
            )

    if target_one_time is not None:
        _audit(
            event_type="OneTimeEntitlementUsed",
            aggregate_type="OneTimeEntitlement",
            aggregate_id=target_one_time.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(new_coverage.id),
                "category": category,
                "reason": "rebind",
            },
            correlation_id=correlation_id,
        )

    if target_makeup is not None:
        _audit(
            event_type="MakeupEntitlementUsed",
            aggregate_type="MakeupEntitlement",
            aggregate_id=target_makeup.id,
            actor=actor,
            payload={
                "attendance_id": str(attendance.id),
                "coverage_id": str(new_coverage.id),
                "allowance_id": str(target_allowance.id),
                "reason": "rebind",
            },
            correlation_id=correlation_id,
        )

    return new_coverage



_ONE_TIME_CATEGORY_BY_TYPE = {
    OneTimeEntitlement.Type.SINGLE_ICE: "ice",
    OneTimeEntitlement.Type.SINGLE_HALL: "hall",
    OneTimeEntitlement.Type.INDIVIDUAL_ICE: "ice",
    OneTimeEntitlement.Type.MINI_GROUP_ICE: "ice",
    OneTimeEntitlement.Type.TRIAL_ICE: "ice",
}


@transaction.atomic
def grant_one_time_entitlement(
    *,
    student_id: UUID,
    lesson_id: UUID,
    entitlement_type: str,
    actor: User | None,
) -> OneTimeEntitlement:
    if entitlement_type not in OneTimeEntitlement.Type.values:
        raise ValidationError(
            {"entitlement_type": "Unsupported one-time entitlement type."}
        )

    lesson = (
        Lesson.objects.select_for_update()
        .select_related("lesson_type")
        .get(pk=lesson_id)
    )
    if lesson.status == Lesson.Status.CANCELLED:
        raise ValidationError(
            {"lesson": "Cannot grant one-time entitlement for CANCELLED lesson."}
        )

    category = lesson.lesson_type.subscription_category
    expected_category = _ONE_TIME_CATEGORY_BY_TYPE[entitlement_type]
    if category != expected_category:
        raise ValidationError(
            {
                "entitlement_type": (
                    "One-time entitlement type does not match lesson category."
                )
            }
        )

    Student.objects.get(pk=student_id)
    entitlement = OneTimeEntitlement.objects.create(
        student_id=student_id,
        lesson=lesson,
        entitlement_type=entitlement_type,
        category=category,
        created_by=actor,
    )

    _audit(
        event_type="OneTimeEntitlementGranted",
        aggregate_type="OneTimeEntitlement",
        aggregate_id=entitlement.id,
        actor=actor,
        payload={
            "student_id": str(student_id),
            "lesson_id": str(lesson.id),
            "entitlement_type": entitlement.entitlement_type,
            "category": entitlement.category,
        },
    )
    return entitlement


@transaction.atomic
def cancel_one_time_entitlement(
    *,
    entitlement_id: UUID,
    actor: User | None,
    at=None,
) -> OneTimeEntitlement:
    entitlement = OneTimeEntitlement.objects.select_for_update().get(
        pk=entitlement_id
    )
    if entitlement.cancelled_at is not None:
        return entitlement

    if AttendanceCoverage.objects.filter(
        one_time_entitlement=entitlement,
        reversed_at__isnull=True,
    ).exists():
        raise ValidationError(
            {
                "entitlement": (
                    "One-time entitlement is used by an active coverage; "
                    "reverse or rebind the coverage first."
                )
            }
        )

    cancelled_at = at or timezone.now()
    entitlement.cancelled_at = cancelled_at
    entitlement.cancelled_by = actor
    entitlement.save(
        update_fields=["cancelled_at", "cancelled_by"]
    )

    _audit(
        event_type="OneTimeEntitlementCancelled",
        aggregate_type="OneTimeEntitlement",
        aggregate_id=entitlement.id,
        actor=actor,
        payload={
            "student_id": str(entitlement.student_id),
            "lesson_id": str(entitlement.lesson_id),
            "entitlement_type": entitlement.entitlement_type,
            "category": entitlement.category,
            "cancelled_at": cancelled_at.isoformat(),
        },
    )
    return entitlement



def _audit_event_exists(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
) -> bool:
    return AuditEvent.objects.filter(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
    ).exists()


@transaction.atomic
def _process_one_subscription_lifecycle(
    *,
    subscription_id: UUID,
    as_of: date,
    actor: User | None,
) -> dict[str, int]:
    result = {
        "activated": 0,
        "expired": 0,
        "expired_with_unused": 0,
    }
    subscription = Subscription.objects.select_for_update().get(
        pk=subscription_id
    )
    if subscription.cancelled_at is not None:
        return result

    if (
        subscription.valid_from <= as_of
        and not _audit_event_exists(
            event_type="SubscriptionActivated",
            aggregate_type="Subscription",
            aggregate_id=subscription.id,
        )
    ):
        _audit(
            event_type="SubscriptionActivated",
            aggregate_type="Subscription",
            aggregate_id=subscription.id,
            actor=actor,
            payload={
                "student_id": str(subscription.student_id),
                "valid_from": subscription.valid_from.isoformat(),
                "valid_until": subscription.valid_until.isoformat(),
                "as_of": as_of.isoformat(),
            },
        )
        result["activated"] = 1

    if subscription.valid_until >= as_of:
        return result

    balances = {
        row["category"]: int(row["balance"] or 0)
        for row in (
            SubscriptionAllowance.objects.filter(subscription=subscription)
            .values("category")
            .annotate(balance=Sum("ledger_entries__delta"))
        )
    }

    if not _audit_event_exists(
        event_type="SubscriptionExpired",
        aggregate_type="Subscription",
        aggregate_id=subscription.id,
    ):
        _audit(
            event_type="SubscriptionExpired",
            aggregate_type="Subscription",
            aggregate_id=subscription.id,
            actor=actor,
            payload={
                "student_id": str(subscription.student_id),
                "valid_until": subscription.valid_until.isoformat(),
                "as_of": as_of.isoformat(),
                "balances": balances,
            },
        )
        result["expired"] = 1

    if (
        any(balance > 0 for balance in balances.values())
        and not _audit_event_exists(
            event_type="SubscriptionExpiredWithUnusedBalance",
            aggregate_type="Subscription",
            aggregate_id=subscription.id,
        )
    ):
        _audit(
            event_type="SubscriptionExpiredWithUnusedBalance",
            aggregate_type="Subscription",
            aggregate_id=subscription.id,
            actor=actor,
            payload={
                "student_id": str(subscription.student_id),
                "valid_until": subscription.valid_until.isoformat(),
                "as_of": as_of.isoformat(),
                "balances": balances,
            },
        )
        result["expired_with_unused"] = 1

    return result


@transaction.atomic
def _process_one_makeup_expiry(
    *,
    makeup_id: UUID,
    as_of: date,
    actor: User | None,
) -> int:
    makeup = MakeupEntitlement.objects.select_for_update().get(pk=makeup_id)
    if makeup.cancelled_at is not None or makeup.valid_until >= as_of:
        return 0
    if AttendanceCoverage.objects.filter(
        makeup_entitlement=makeup,
        reversed_at__isnull=True,
    ).exists():
        return 0
    if _audit_event_exists(
        event_type="MakeupEntitlementExpired",
        aggregate_type="MakeupEntitlement",
        aggregate_id=makeup.id,
    ):
        return 0

    _audit(
        event_type="MakeupEntitlementExpired",
        aggregate_type="MakeupEntitlement",
        aggregate_id=makeup.id,
        actor=actor,
        payload={
            "student_id": str(makeup.student_id),
            "source_lesson_id": str(makeup.source_lesson_id),
            "source_allowance_id": str(
                makeup.source_subscription_allowance_id
            ),
            "category": makeup.category,
            "valid_until": makeup.valid_until.isoformat(),
            "as_of": as_of.isoformat(),
        },
    )
    return 1


def process_subscription_lifecycle(
    *,
    as_of: date,
    actor: User | None = None,
) -> dict[str, int]:
    """Emit derived lifecycle events using short per-object transactions."""
    counts = {
        "activated": 0,
        "expired": 0,
        "expired_with_unused": 0,
        "makeup_expired": 0,
    }

    activated_event = AuditEvent.objects.filter(
        event_type="SubscriptionActivated",
        aggregate_type="Subscription",
        aggregate_id=OuterRef("pk"),
    )
    expired_event = AuditEvent.objects.filter(
        event_type="SubscriptionExpired",
        aggregate_type="Subscription",
        aggregate_id=OuterRef("pk"),
    )
    unused_event = AuditEvent.objects.filter(
        event_type="SubscriptionExpiredWithUnusedBalance",
        aggregate_type="Subscription",
        aggregate_id=OuterRef("pk"),
    )
    subscription_ids = list(
        Subscription.objects.filter(
            cancelled_at__isnull=True,
            valid_from__lte=as_of,
        )
        .annotate(
            has_activated_event=Exists(activated_event),
            has_expired_event=Exists(expired_event),
            has_unused_event=Exists(unused_event),
            total_balance=Sum("allowances__ledger_entries__delta"),
        )
        .filter(
            Q(has_activated_event=False)
            | Q(
                valid_until__lt=as_of,
                has_expired_event=False,
            )
            | Q(
                valid_until__lt=as_of,
                has_unused_event=False,
                total_balance__gt=0,
            )
        )
        .order_by("id")
        .values_list("id", flat=True)
    )

    for subscription_id in subscription_ids:
        result = _process_one_subscription_lifecycle(
            subscription_id=subscription_id,
            as_of=as_of,
            actor=actor,
        )
        for key, value in result.items():
            counts[key] += value

    active_makeup_usage = AttendanceCoverage.objects.filter(
        makeup_entitlement_id=OuterRef("pk"),
        reversed_at__isnull=True,
    )
    expired_makeup_event = AuditEvent.objects.filter(
        event_type="MakeupEntitlementExpired",
        aggregate_type="MakeupEntitlement",
        aggregate_id=OuterRef("pk"),
    )
    makeup_ids = list(
        MakeupEntitlement.objects.filter(
            valid_until__lt=as_of,
            cancelled_at__isnull=True,
        )
        .annotate(
            is_used=Exists(active_makeup_usage),
            has_expired_event=Exists(expired_makeup_event),
        )
        .filter(
            is_used=False,
            has_expired_event=False,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    for makeup_id in makeup_ids:
        counts["makeup_expired"] += _process_one_makeup_expiry(
            makeup_id=makeup_id,
            as_of=as_of,
            actor=actor,
        )

    return counts
