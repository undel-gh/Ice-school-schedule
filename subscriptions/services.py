from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from accounts.models import Student
from attendance.models import AbsenceJustification, Attendance
from audit.models import AuditEvent
from audit.services import event_exists, record_event
from core.permissions import require_permission
from core.time import school_date
from scheduling.models import Lesson

from .balances import (
    ledger_balance,
    locked_allowance_balance,
    locked_eligible_source_allowance,
)
from .models import (
    AbsenceCompensationActionGrant,
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from .selectors import (
    get_applicable_absence_policy,
    resolve_compensation_actions,
)

User = get_user_model()


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
    record_event(**values)




def _resolved_action_snapshot(resolved) -> list[dict]:
    return [
        {
            "action_id": str(item.action.id),
            "action_type": item.action.action_type,
            "target_period_rule": item.action.target_period_rule,
            "requirement": item.requirement,
            "validity_days": item.action.validity_days,
            "priority": item.action.priority,
            "window_id": str(item.window.id) if item.window else None,
            "window_name": item.window.name if item.window else None,
            "target_from": (
                item.target_from.isoformat()
                if item.target_from is not None
                else None
            ),
            "target_until": (
                item.target_until.isoformat()
                if item.target_until is not None
                else None
            ),
        }
        for item in resolved
    ]




def _locked_source_allowance_for_absence(
    *,
    attendance: Attendance,
    category: str,
    source_date: date,
) -> SubscriptionAllowance | None:
    """
    Resolve the historical allowance associated with the missed lesson.

    Unlike coverage assignment, compensation provenance does not require a
    positive current balance. A fully consumed allowance can still be the
    correct source for explaining the absence.
    """
    candidate_ids = list(
        SubscriptionAllowance.objects.filter(
            subscription__student_id=attendance.student_id,
            category=category,
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
        allowance = (
            SubscriptionAllowance.objects.select_for_update()
            .select_related("subscription")
            .get(pk=allowance_id)
        )
        subscription = allowance.subscription
        if subscription.student_id != attendance.student_id:
            continue
        if allowance.category != category:
            continue
        if not (
            subscription.valid_from <= source_date <= subscription.valid_until
        ):
            continue
        if (
            subscription.cancelled_at is not None
            and subscription.cancelled_at <= attendance.lesson.starts_at
        ):
            continue
        return allowance
    return None



def _eligibility_period_for_case(
    case: AbsenceCompensationCase,
) -> tuple[UUID, date, date] | None:
    if case.source_subscription_allowance_id is None:
        return None
    allowance = (
        SubscriptionAllowance.objects.select_related("subscription")
        .get(pk=case.source_subscription_allowance_id)
    )
    subscription = allowance.subscription
    return subscription.id, subscription.valid_from, subscription.valid_until


def _case_limit_peers(
    *,
    case: AbsenceCompensationCase,
    source_subscription_id: UUID,
):
    peers = AbsenceCompensationCase.objects.filter(
        student_id=case.student_id,
        status__in=[
            AbsenceCompensationCase.Status.OPEN,
            AbsenceCompensationCase.Status.MATERIALIZED,
        ],
        absence_reason=case.absence_reason,
        policy_code_snapshot=case.policy_code_snapshot,
        source_subscription_allowance__subscription_id=source_subscription_id,
    )
    if (
        case.limit_scope_snapshot
        == AbsenceCompensationPolicy.LimitScope.CATEGORY_PERIOD
    ):
        peers = peers.filter(category=case.category)
    elif (
        case.limit_scope_snapshot
        == AbsenceCompensationPolicy.LimitScope.LESSON_TYPE_PERIOD
    ):
        peers = peers.filter(
            source_lesson__lesson_type_id=case.source_lesson.lesson_type_id
        )
    return peers.select_related("source_lesson").order_by(
        "source_lesson__starts_at",
        "source_lesson_id",
        "id",
    )


def _evaluate_case_eligibility(
    *,
    case: AbsenceCompensationCase,
    actor: User | None,
    evaluated_at,
) -> bool:
    previous = (
        case.eligibility_status,
        case.eligible_absence_ordinal,
        case.eligibility_period_from,
        case.eligibility_period_until,
    )

    limit = case.max_eligible_absences_snapshot
    if limit is None:
        case.eligibility_status = (
            AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
        )
        case.eligible_absence_ordinal = None
        case.eligibility_period_from = None
        case.eligibility_period_until = None
    else:
        period = _eligibility_period_for_case(case)
        if period is None:
            case.eligibility_status = (
                AbsenceCompensationCase.EligibilityStatus.UNDETERMINED
            )
            case.eligible_absence_ordinal = None
            case.eligibility_period_from = None
            case.eligibility_period_until = None
        else:
            source_subscription_id, period_from, period_until = period
            peers = _case_limit_peers(
                case=case,
                source_subscription_id=source_subscription_id,
            )
            materialized_count = peers.filter(
                status=AbsenceCompensationCase.Status.MATERIALIZED,
            ).count()
            open_ids = list(
                peers.filter(
                    status=AbsenceCompensationCase.Status.OPEN,
                ).values_list("id", flat=True)
            )
            try:
                ordinal = materialized_count + open_ids.index(case.id) + 1
            except ValueError as exc:
                raise ValidationError(
                    {
                        "case": (
                            "Open compensation case is missing from its "
                            "eligibility scope."
                        )
                    }
                ) from exc

            case.eligible_absence_ordinal = ordinal
            case.eligibility_period_from = period_from
            case.eligibility_period_until = period_until
            case.eligibility_status = (
                AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
                if ordinal <= limit
                else AbsenceCompensationCase.EligibilityStatus.LIMIT_EXCEEDED
            )

    case.eligibility_evaluated_at = evaluated_at
    case.save(
        update_fields=[
            "eligibility_status",
            "eligible_absence_ordinal",
            "eligibility_period_from",
            "eligibility_period_until",
            "eligibility_evaluated_at",
        ]
    )

    current = (
        case.eligibility_status,
        case.eligible_absence_ordinal,
        case.eligibility_period_from,
        case.eligibility_period_until,
    )
    changed = current != previous
    if changed:
        _audit(
            event_type="AbsenceCompensationEvaluated",
            aggregate_type="AbsenceCompensationCase",
            aggregate_id=case.id,
            actor=actor,
            payload={
                "eligibility_status": case.eligibility_status,
                "eligible_absence_ordinal": case.eligible_absence_ordinal,
                "max_eligible_absences": limit,
                "limit_scope": case.limit_scope_snapshot,
                "period_from": (
                    case.eligibility_period_from.isoformat()
                    if case.eligibility_period_from is not None
                    else None
                ),
                "period_until": (
                    case.eligibility_period_until.isoformat()
                    if case.eligibility_period_until is not None
                    else None
                ),
            },
        )
    return changed


def _reevaluate_open_compensation_cases_for_student(
    *,
    student_id: UUID,
    actor: User | None,
    evaluated_at,
) -> None:
    cases = list(
        AbsenceCompensationCase.objects.select_for_update()
        .filter(
            student_id=student_id,
            status=AbsenceCompensationCase.Status.OPEN,
        )
        .select_related(
            "source_lesson__lesson_type",
        )
        .order_by("source_lesson__starts_at", "source_lesson_id", "id")
    )
    for case in cases:
        _evaluate_case_eligibility(
            case=case,
            actor=actor,
            evaluated_at=evaluated_at,
        )


@transaction.atomic
def create_absence_compensation_case(
    *,
    attendance_id: UUID,
    absence_reason: str,
    actor: User,
    policy_code: str | None = None,
    source_justification_id: UUID | None = None,
    now=None,
) -> AbsenceCompensationCase:
    require_permission(
        actor,
        "subscriptions.add_absencecompensationcase",
        "Absence compensation case permission is required.",
    )
    if absence_reason not in AbsenceCompensationPolicy.AbsenceReason.values:
        raise ValidationError(
            {"absence_reason": "Unsupported absence reason."}
        )

    attendance = (
        Attendance.objects.select_for_update()
        .select_related("lesson__lesson_type", "student")
        .get(pk=attendance_id)
    )
    if attendance.status != Attendance.Status.ABSENT:
        raise ValidationError(
            {"attendance": "Compensation requires Attendance=ABSENT."}
        )

    Student.objects.select_for_update().get(pk=attendance.student_id)

    existing = (
        AbsenceCompensationCase.objects.select_for_update()
        .filter(
            attendance=attendance,
            status=AbsenceCompensationCase.Status.OPEN,
        )
        .first()
    )
    if existing is not None:
        if existing.absence_reason != absence_reason:
            raise ValidationError(
                {
                    "attendance": (
                        "An open compensation case already exists with a "
                        "different absence reason. Cancel it before creating "
                        "a replacement case."
                    )
                }
            )
        return existing

    lesson = attendance.lesson
    source_date = school_date(lesson.starts_at)
    policy = get_applicable_absence_policy(
        absence_reason=absence_reason,
        source_date=source_date,
        policy_code=policy_code,
    )
    if policy is None:
        raise ValidationError(
            {"policy": "No active compensation policy matches this absence."}
        )

    source_justification = None
    if source_justification_id is not None:
        source_justification = (
            AbsenceJustification.objects.select_for_update()
            .get(pk=source_justification_id)
        )
        if (
            source_justification.student_id != attendance.student_id
            or source_justification.lesson_id != attendance.lesson_id
        ):
            raise ValidationError(
                {
                    "source_justification": (
                        "Justification does not belong to this absence."
                    )
                }
            )

    if (
        policy.justification_requirement
        == AbsenceCompensationPolicy.JustificationRequirement.VERIFIED_MEDICAL
    ):
        if source_justification is None:
            source_justification = (
                AbsenceJustification.objects.select_for_update()
                .filter(
                    student_id=attendance.student_id,
                    lesson_id=attendance.lesson_id,
                    type=AbsenceJustification.Type.MEDICAL,
                    status=AbsenceJustification.Status.VERIFIED,
                )
                .order_by("-reviewed_at", "id")
                .first()
            )
        if (
            source_justification is None
            or source_justification.status
            != AbsenceJustification.Status.VERIFIED
        ):
            raise ValidationError(
                {
                    "source_justification": (
                        "A verified medical justification is required."
                    )
                }
            )

    category = lesson.lesson_type.subscription_category
    source_allowance = _locked_source_allowance_for_absence(
        attendance=attendance,
        category=category,
        source_date=source_date,
    )

    resolved = resolve_compensation_actions(
        policy=policy,
        source_date=source_date,
    )

    case = AbsenceCompensationCase.objects.create(
        attendance=attendance,
        student_id=attendance.student_id,
        source_lesson_id=attendance.lesson_id,
        source_subscription_allowance=source_allowance,
        source_justification=source_justification,
        policy=policy,
        absence_reason=absence_reason,
        source_date=source_date,
        category=category,
        policy_code_snapshot=policy.code,
        policy_version_snapshot=policy.version,
        policy_name_snapshot=policy.name,
        justification_requirement_snapshot=(
            policy.justification_requirement
        ),
        max_eligible_absences_snapshot=policy.max_eligible_absences,
        limit_scope_snapshot=policy.limit_scope,
        actions_snapshot=_resolved_action_snapshot(resolved),
        created_by=actor,
    )
    evaluated_at = now or timezone.now()
    _reevaluate_open_compensation_cases_for_student(
        student_id=attendance.student_id,
        actor=actor,
        evaluated_at=evaluated_at,
    )
    case.refresh_from_db()
    _audit(
        event_type="AbsenceCompensationCaseCreated",
        aggregate_type="AbsenceCompensationCase",
        aggregate_id=case.id,
        actor=actor,
        payload={
            "attendance_id": str(attendance.id),
            "student_id": str(attendance.student_id),
            "source_lesson_id": str(attendance.lesson_id),
            "source_subscription_allowance_id": (
                str(source_allowance.id)
                if source_allowance is not None
                else None
            ),
            "source_justification_id": (
                str(source_justification.id)
                if source_justification is not None
                else None
            ),
            "absence_reason": absence_reason,
            "policy_code": policy.code,
            "policy_version": policy.version,
            "action_count": len(case.actions_snapshot),
            "eligibility_status": case.eligibility_status,
            "eligible_absence_ordinal": case.eligible_absence_ordinal,
        },
    )
    return case


def _cancel_open_absence_compensation_cases(
    *,
    attendance_id: UUID,
    actor: User | None,
    at,
    reason: str,
    source_justification_id: UUID | None = None,
) -> int:
    attendance = Attendance.objects.select_for_update().get(pk=attendance_id)
    Student.objects.select_for_update().get(pk=attendance.student_id)
    cases = AbsenceCompensationCase.objects.select_for_update().filter(
        attendance_id=attendance.id,
        status=AbsenceCompensationCase.Status.OPEN,
    )
    if source_justification_id is not None:
        cases = cases.filter(source_justification_id=source_justification_id)

    cancelled = 0
    for case in list(cases.order_by("id")):
        case.status = AbsenceCompensationCase.Status.CANCELLED
        case.cancelled_at = at
        case.cancelled_by = actor
        case.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
            ]
        )
        _audit(
            event_type="AbsenceCompensationCaseCancelled",
            aggregate_type="AbsenceCompensationCase",
            aggregate_id=case.id,
            actor=actor,
            payload={
                "attendance_id": str(case.attendance_id),
                "cancelled_at": at.isoformat(),
                "reason": reason,
            },
        )
        cancelled += 1

    if cancelled:
        _reevaluate_open_compensation_cases_for_student(
            student_id=attendance.student_id,
            actor=actor,
            evaluated_at=at,
        )
    return cancelled


def _case_action_snapshot(
    *,
    case: AbsenceCompensationCase,
    action_type: str,
) -> dict | None:
    for item in case.actions_snapshot:
        if item.get("action_type") == action_type:
            return item
    return None


@transaction.atomic
def materialize_free_makeup_from_case(
    *,
    case_id: UUID,
    actor: User,
    now=None,
) -> AbsenceCompensationActionGrant:
    require_permission(
        actor,
        "subscriptions.add_makeupentitlement",
        "Make-up entitlement permission is required.",
    )
    require_permission(
        actor,
        "subscriptions.change_absencecompensationcase",
        "Compensation case change permission is required.",
    )

    case_ref = AbsenceCompensationCase.objects.only(
        "student_id",
    ).get(pk=case_id)
    Student.objects.select_for_update().get(pk=case_ref.student_id)

    existing = (
        AbsenceCompensationActionGrant.objects.select_for_update()
        .select_related("makeup_entitlement")
        .filter(
            case_id=case_id,
            action_type="free_makeup",
        )
        .first()
    )
    if existing is not None:
        return existing

    case = (
        AbsenceCompensationCase.objects.select_for_update()
        .select_related(
            "attendance",
            "source_lesson__lesson_type",
            "source_subscription_allowance__subscription",
        )
        .get(pk=case_id)
    )
    if case.status != AbsenceCompensationCase.Status.OPEN:
        raise ValidationError(
            {"case": "Only an OPEN compensation case can be materialized."}
        )
    if (
        case.eligibility_status
        != AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    ):
        raise ValidationError(
            {
                "case": (
                    "Compensation case must be ELIGIBLE before "
                    "materialization."
                )
            }
        )
    if case.attendance.status != Attendance.Status.ABSENT:
        raise ValidationError(
            {"attendance": "Source attendance is no longer ABSENT."}
        )

    action = _case_action_snapshot(
        case=case,
        action_type="free_makeup",
    )
    if action is None:
        raise ValidationError(
            {"case": "FREE_MAKEUP is not allowed by this case policy."}
        )
    if action.get("requirement") not in (None, "", "none"):
        raise ValidationError(
            {
                "case": (
                    "FREE_MAKEUP action has unmet additional requirements."
                )
            }
        )

    allowance = case.source_subscription_allowance
    if allowance is None:
        raise ValidationError(
            {
                "case": (
                    "FREE_MAKEUP requires a source subscription allowance."
                )
            }
        )
    locked_allowance, balance = locked_allowance_balance(allowance.id)
    allowance = locked_allowance
    subscription = Subscription.objects.select_for_update().get(
        pk=allowance.subscription_id
    )
    if subscription.cancelled_at is not None:
        raise ValidationError(
            {"case": "Source subscription is cancelled."}
        )
    if balance <= 0:
        raise ValidationError(
            {"case": "Source allowance has no remaining visits."}
        )

    target_rule = action.get("target_period_rule")
    target_from = action.get("target_from")
    target_until = action.get("target_until")

    if target_rule == "current_period":
        valid_from = case.source_date
        valid_until = subscription.valid_until
    elif target_rule == "next_student_period":
        raise ValidationError(
            {
                "case": (
                    "NEXT_STUDENT_PERIOD materialization requires the "
                    "subscription-period model and is not implemented yet."
                )
            }
        )
    elif target_rule == "explicit_target_window":
        if not target_from or not target_until:
            raise ValidationError(
                {"case": "Explicit target window is missing."}
            )
        valid_from = date.fromisoformat(target_from)
        valid_until = date.fromisoformat(target_until)
    else:
        raise ValidationError(
            {"case": "Unsupported target-period rule."}
        )

    validity_days = action.get("validity_days")
    if validity_days is not None:
        bounded_until = valid_from + timedelta(days=int(validity_days) - 1)
        if bounded_until < valid_until:
            valid_until = bounded_until

    materialized_at = now or timezone.now()
    entitlement = MakeupEntitlement.objects.create(
        student_id=case.student_id,
        source_lesson_id=case.source_lesson_id,
        source_subscription_allowance=allowance,
        source_justification=case.source_justification,
        category=case.category,
        reason=MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
        valid_from=valid_from,
        valid_until=valid_until,
        created_by=actor,
    )

    grant = AbsenceCompensationActionGrant.objects.create(
        case=case,
        action_type="free_makeup",
        action_snapshot=action,
        makeup_entitlement=entitlement,
        created_by=actor,
    )

    case.status = AbsenceCompensationCase.Status.MATERIALIZED
    case.materialized_at = materialized_at
    case.materialized_by = actor
    case.save(
        update_fields=[
            "status",
            "materialized_at",
            "materialized_by",
        ]
    )

    correlation_id = uuid4()
    _audit(
        event_type="AbsenceCompensationMaterialized",
        aggregate_type="AbsenceCompensationCase",
        aggregate_id=case.id,
        actor=actor,
        correlation_id=correlation_id,
        payload={
            "action_type": grant.action_type,
            "grant_id": str(grant.id),
            "makeup_entitlement_id": str(entitlement.id),
            "eligibility_status": case.eligibility_status,
            "eligible_absence_ordinal": case.eligible_absence_ordinal,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
        },
    )
    _audit(
        event_type="MakeupEntitlementGranted",
        aggregate_type="MakeupEntitlement",
        aggregate_id=entitlement.id,
        actor=actor,
        correlation_id=correlation_id,
        payload={
            "student_id": str(case.student_id),
            "source_lesson_id": str(case.source_lesson_id),
            "source_allowance_id": str(allowance.id),
            "category": case.category,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
            "reason": entitlement.reason,
            "compensation_case_id": str(case.id),
            "compensation_grant_id": str(grant.id),
        },
    )
    return grant


@transaction.atomic
def cancel_absence_compensation_case(
    *,
    case_id: UUID,
    actor: User,
    at=None,
) -> AbsenceCompensationCase:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationcase",
        "Absence compensation case change permission is required.",
    )
    case_ref = AbsenceCompensationCase.objects.only(
        "student_id",
    ).get(pk=case_id)
    Student.objects.select_for_update().get(pk=case_ref.student_id)
    case = AbsenceCompensationCase.objects.select_for_update().get(pk=case_id)
    if case.status == AbsenceCompensationCase.Status.CANCELLED:
        return case
    if case.status == AbsenceCompensationCase.Status.MATERIALIZED:
        raise ValidationError(
            {
                "case": (
                    "A MATERIALIZED compensation case cannot be cancelled "
                    "directly. Reverse its materialized actions first."
                )
            }
        )

    at = at or timezone.now()
    case.status = AbsenceCompensationCase.Status.CANCELLED
    case.cancelled_at = at
    case.cancelled_by = actor
    case.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
        ]
    )
    _audit(
        event_type="AbsenceCompensationCaseCancelled",
        aggregate_type="AbsenceCompensationCase",
        aggregate_id=case.id,
        actor=actor,
        payload={
            "attendance_id": str(case.attendance_id),
            "cancelled_at": at.isoformat(),
        },
    )
    _reevaluate_open_compensation_cases_for_student(
        student_id=case.student_id,
        actor=actor,
        evaluated_at=at,
    )
    return case


@transaction.atomic
def issue_subscription(
    *,
    student_id: UUID,
    plan_id: UUID,
    valid_from: date,
    valid_until: date,
    actor: User,
) -> Subscription:
    require_permission(
        actor,
        "subscriptions.add_subscription",
        "Subscription issue permission is required.",
    )
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

        allowance, balance = locked_allowance_balance(
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
        allowance, balance = locked_allowance_balance(allowance_id)

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
        allowance, _ = locked_allowance_balance(
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
    require_permission(
        actor,
        "subscriptions.change_subscriptionallowance",
        "Subscription allowance adjustment permission is required.",
    )
    if delta == 0:
        raise ValidationError(
            {"delta": "Adjustment delta must be non-zero."}
        )
    if not reason.strip():
        raise ValidationError(
            {"reason": "Adjustment reason is required."}
        )

    allowance, balance = locked_allowance_balance(allowance_id)
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
    require_permission(
        actor,
        "subscriptions.change_subscription",
        "Subscription cancellation permission is required.",
    )
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
    require_permission(
        actor,
        "subscriptions.add_makeupentitlement",
        "Administrative make-up grant permission is required.",
    )


@transaction.atomic
def apply_school_reschedule_entitlements(
    *,
    source_lesson_id: UUID,
    replacement_lesson_id: UUID,
    actor: User,
) -> tuple[MakeupEntitlement, ...]:
    source = (
        Lesson.objects.select_for_update()
        .select_related("lesson_type")
        .get(pk=source_lesson_id)
    )
    replacement = Lesson.objects.select_for_update().get(
        pk=replacement_lesson_id
    )
    if source.replacement_lesson_id != replacement.id:
        raise ValidationError(
            {
                "replacement_lesson": (
                    "Replacement lesson is not linked from the source lesson."
                )
            }
        )

    source_date = school_date(source.starts_at)
    replacement_date = school_date(replacement.starts_at)
    category = source.lesson_type.subscription_category

    one_time_ids = list(
        OneTimeEntitlement.objects.filter(
            lesson=source,
            category=category,
            cancelled_at__isnull=True,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    for entitlement_id in one_time_ids:
        entitlement = OneTimeEntitlement.objects.select_for_update().get(
            pk=entitlement_id
        )
        if AttendanceCoverage.objects.filter(
            one_time_entitlement=entitlement,
            reversed_at__isnull=True,
        ).exists():
            continue
        require_permission(
            actor,
            "subscriptions.change_onetimeentitlement",
            "One-time entitlement transfer permission is required.",
        )
        entitlement.lesson = replacement
        entitlement.save(update_fields=["lesson"])
        record_event(
            event_type="OneTimeEntitlementTransferred",
            aggregate_type="OneTimeEntitlement",
            aggregate_id=entitlement.id,
            actor=actor,
            payload={
                "student_id": str(entitlement.student_id),
                "source_lesson_id": str(source.id),
                "replacement_lesson_id": str(replacement.id),
                "category": entitlement.category,
                "entitlement_type": entitlement.entitlement_type,
            },
        )

    yes_student_ids = list(
        source.responses.filter(
            status="yes",
        ).values_list("student_id", flat=True)
    )

    created: list[MakeupEntitlement] = []
    for student_id in yes_student_ids:
        selected = locked_eligible_source_allowance(
            student_id=student_id,
            category=category,
            source_date=source_date,
        )
        if selected is None:
            continue

        allowance, subscription, _ = selected
        if (
            subscription.valid_from
            <= replacement_date
            <= subscription.valid_until
        ):
            continue

        require_permission(
            actor,
            "subscriptions.add_makeupentitlement",
            "School reschedule make-up permission is required.",
        )
        entitlement, was_created = MakeupEntitlement.objects.get_or_create(
            student_id=student_id,
            source_lesson=source,
            reason=MakeupEntitlement.Reason.SCHOOL_RESCHEDULE,
            defaults={
                "source_subscription_allowance": allowance,
                "category": category,
                "valid_from": replacement_date,
                "valid_until": replacement_date,
                "target_lesson": replacement,
                "created_by": actor,
            },
        )
        if not was_created:
            continue

        record_event(
            event_type="MakeupEntitlementGranted",
            aggregate_type="MakeupEntitlement",
            aggregate_id=entitlement.id,
            actor=actor,
            payload={
                "student_id": str(student_id),
                "source_lesson_id": str(source.id),
                "replacement_lesson_id": str(replacement.id),
                "source_allowance_id": str(allowance.id),
                "category": category,
                "valid_from": replacement_date.isoformat(),
                "valid_until": replacement_date.isoformat(),
                "reason": entitlement.reason,
            },
        )
        created.append(entitlement)

    return tuple(created)


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

    allowance, balance = locked_allowance_balance(
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
    require_permission(
        actor,
        "subscriptions.change_attendancecoverage",
        "Attendance coverage rebind permission is required.",
    )

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
        target_balance = ledger_balance(target_allowance.id)
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
    actor: User,
) -> OneTimeEntitlement:
    require_permission(
        actor,
        "subscriptions.add_onetimeentitlement",
        "One-time entitlement grant permission is required.",
    )
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
    actor: User,
    at=None,
) -> OneTimeEntitlement:
    require_permission(
        actor,
        "subscriptions.change_onetimeentitlement",
        "One-time entitlement cancellation permission is required.",
    )
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
        and not event_exists(
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

    if not event_exists(
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
        and not event_exists(
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
    if event_exists(
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
