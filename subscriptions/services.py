from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from accounts.models import Student
from attendance.models import AbsenceJustification, Attendance
from audit.models import AuditEvent
from audit.services import event_exists, record_event
from core.choices import SubscriptionCategory
from core.permissions import require_permission
from core.time import school_date
from scheduling.models import Lesson, TrainingGroup

from .balances import (
    ledger_balance,
    locked_allowance_balance,
    locked_eligible_source_allowance,
)
from .models import (
    AbsenceCompensationActionGrant,
    AbsenceCompensationCase,
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
    AttendanceCoverage,
    GroupPlaceHold,
    MakeupEntitlement,
    OneTimeEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
    SubscriptionPeriod,
    SubscriptionPeriodScheme,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from .selectors import (
    get_applicable_absence_policy,
    resolve_compensation_actions,
    usable_makeups_for_subscription,
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





def _clean_catalog_text(value: str, *, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError({field: "This field is required."})
    return value


def _validate_plan_allowances(
    allowances: dict[str, int | None],
) -> dict[str, int]:
    allowed_categories = set(SubscriptionCategory.values)
    unknown = set(allowances) - allowed_categories
    if unknown:
        raise ValidationError(
            {"allowances": f"Unsupported allowance categories: {sorted(unknown)!r}."}
        )
    normalized = {}
    for category, value in allowances.items():
        if value in (None, ""):
            continue
        value = int(value)
        if value <= 0:
            raise ValidationError(
                {category: "Visit limit must be greater than zero."}
            )
        normalized[category] = value
    if not normalized:
        raise ValidationError(
            {"allowances": "Subscription plan requires at least one allowance."}
        )
    return normalized


@transaction.atomic
def create_subscription_period_scheme(
    *,
    code: str,
    name: str,
    mode: str,
    fixed_anchor_date: date | None,
    is_active: bool,
    actor: User,
) -> SubscriptionPeriodScheme:
    require_permission(
        actor,
        "subscriptions.add_subscriptionperiodscheme",
        "Subscription period scheme creation permission is required.",
    )
    if mode not in SubscriptionPeriodScheme.Mode.values:
        raise ValidationError({"mode": "Unsupported subscription period mode."})
    scheme = SubscriptionPeriodScheme(
        code=_clean_catalog_text(code, field="code"),
        name=_clean_catalog_text(name, field="name"),
        mode=mode,
        fixed_anchor_date=fixed_anchor_date,
        is_active=is_active,
    )
    scheme.full_clean()
    try:
        scheme.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "A period scheme with this code already exists."}
        ) from exc
    _audit(
        event_type="SubscriptionPeriodSchemeCreated",
        aggregate_type="SubscriptionPeriodScheme",
        aggregate_id=scheme.id,
        actor=actor,
        payload={
            "code": scheme.code,
            "name": scheme.name,
            "mode": scheme.mode,
            "fixed_anchor_date": (
                scheme.fixed_anchor_date.isoformat()
                if scheme.fixed_anchor_date is not None
                else None
            ),
            "is_active": scheme.is_active,
        },
    )
    return scheme


@transaction.atomic
def update_subscription_period_scheme(
    *,
    scheme_id: UUID,
    code: str,
    name: str,
    mode: str,
    fixed_anchor_date: date | None,
    is_active: bool,
    actor: User,
) -> SubscriptionPeriodScheme:
    require_permission(
        actor,
        "subscriptions.change_subscriptionperiodscheme",
        "Subscription period scheme change permission is required.",
    )
    if mode not in SubscriptionPeriodScheme.Mode.values:
        raise ValidationError({"mode": "Unsupported subscription period mode."})
    scheme = SubscriptionPeriodScheme.objects.select_for_update().get(
        pk=scheme_id
    )
    referenced = (
        SubscriptionPlan.objects.filter(period_scheme=scheme).exists()
        or SubscriptionPeriod.objects.filter(scheme=scheme).exists()
    )
    if referenced and (
        scheme.mode != mode
        or scheme.fixed_anchor_date != fixed_anchor_date
    ):
        raise ValidationError(
            {
                "mode": (
                    "Referenced period schemes cannot change mode or fixed "
                    "anchor. Create a new scheme and switch future plans to it."
                )
            }
        )
    if (
        scheme.is_active
        and not is_active
        and SubscriptionPlan.objects.filter(
            period_scheme=scheme,
            is_active=True,
        ).exists()
    ):
        raise ValidationError(
            {
                "is_active": (
                    "Deactivate or move active subscription plans before "
                    "deactivating this period scheme."
                )
            }
        )
    previous = {
        "code": scheme.code,
        "name": scheme.name,
        "mode": scheme.mode,
        "fixed_anchor_date": (
            scheme.fixed_anchor_date.isoformat()
            if scheme.fixed_anchor_date is not None
            else None
        ),
        "is_active": scheme.is_active,
    }
    scheme.code = _clean_catalog_text(code, field="code")
    scheme.name = _clean_catalog_text(name, field="name")
    scheme.mode = mode
    scheme.fixed_anchor_date = fixed_anchor_date
    scheme.is_active = is_active
    scheme.full_clean()
    try:
        scheme.save(
            update_fields=[
                "code",
                "name",
                "mode",
                "fixed_anchor_date",
                "is_active",
                "updated_at",
            ]
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "A period scheme with this code already exists."}
        ) from exc
    _audit(
        event_type="SubscriptionPeriodSchemeChanged",
        aggregate_type="SubscriptionPeriodScheme",
        aggregate_id=scheme.id,
        actor=actor,
        payload={
            "previous": previous,
            "code": scheme.code,
            "name": scheme.name,
            "mode": scheme.mode,
            "fixed_anchor_date": (
                scheme.fixed_anchor_date.isoformat()
                if scheme.fixed_anchor_date is not None
                else None
            ),
            "is_active": scheme.is_active,
        },
    )
    return scheme


def _replace_plan_allowances_locked(
    *,
    plan: SubscriptionPlan,
    allowances: dict[str, int],
) -> None:
    existing = {
        item.category: item
        for item in SubscriptionPlanAllowance.objects.select_for_update()
        .filter(plan=plan)
        .order_by("category", "id")
    }
    for category, visit_limit in allowances.items():
        item = existing.pop(category, None)
        if item is None:
            SubscriptionPlanAllowance.objects.create(
                plan=plan,
                category=category,
                visit_limit=visit_limit,
            )
        elif item.visit_limit != visit_limit:
            item.visit_limit = visit_limit
            item.save(update_fields=["visit_limit"])
    if existing:
        SubscriptionPlanAllowance.objects.filter(
            id__in=[item.id for item in existing.values()]
        ).delete()


@transaction.atomic
def create_subscription_plan(
    *,
    code: str,
    name: str,
    period_scheme_id: UUID | None,
    is_active: bool,
    allowances: dict[str, int | None],
    actor: User,
) -> SubscriptionPlan:
    require_permission(
        actor,
        "subscriptions.add_subscriptionplan",
        "Subscription plan creation permission is required.",
    )
    normalized = _validate_plan_allowances(allowances)
    scheme = None
    if period_scheme_id is not None:
        scheme = SubscriptionPeriodScheme.objects.select_for_update().get(
            pk=period_scheme_id
        )
        if is_active and not scheme.is_active:
            raise ValidationError(
                {"period_scheme": "An active plan requires an active period scheme."}
            )
    elif is_active:
        raise ValidationError(
            {"period_scheme": "An active plan requires a period scheme."}
        )

    plan = SubscriptionPlan(
        code=_clean_catalog_text(code, field="code"),
        name=_clean_catalog_text(name, field="name"),
        validity_months=1,
        period_scheme=scheme,
        is_active=is_active,
    )
    plan.full_clean()
    try:
        plan.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "A subscription plan with this code already exists."}
        ) from exc
    _replace_plan_allowances_locked(plan=plan, allowances=normalized)
    _audit(
        event_type="SubscriptionPlanCreated",
        aggregate_type="SubscriptionPlan",
        aggregate_id=plan.id,
        actor=actor,
        payload={
            "code": plan.code,
            "name": plan.name,
            "period_scheme_id": (
                str(plan.period_scheme_id) if plan.period_scheme_id else None
            ),
            "is_active": plan.is_active,
            "allowances": normalized,
        },
    )
    return plan


@transaction.atomic
def update_subscription_plan(
    *,
    plan_id: UUID,
    code: str,
    name: str,
    period_scheme_id: UUID | None,
    is_active: bool,
    allowances: dict[str, int | None],
    actor: User,
) -> SubscriptionPlan:
    require_permission(
        actor,
        "subscriptions.change_subscriptionplan",
        "Subscription plan change permission is required.",
    )
    normalized = _validate_plan_allowances(allowances)
    plan = SubscriptionPlan.objects.select_for_update().get(pk=plan_id)
    scheme = None
    if period_scheme_id is not None:
        scheme = SubscriptionPeriodScheme.objects.select_for_update().get(
            pk=period_scheme_id
        )
        if is_active and not scheme.is_active:
            raise ValidationError(
                {"period_scheme": "An active plan requires an active period scheme."}
            )
    elif is_active:
        raise ValidationError(
            {"period_scheme": "An active plan requires a period scheme."}
        )
    previous_allowances = {
        item.category: item.visit_limit
        for item in SubscriptionPlanAllowance.objects.select_for_update()
        .filter(plan=plan)
        .order_by("category", "id")
    }
    previous = {
        "code": plan.code,
        "name": plan.name,
        "period_scheme_id": (
            str(plan.period_scheme_id) if plan.period_scheme_id else None
        ),
        "is_active": plan.is_active,
        "allowances": previous_allowances,
    }
    plan.code = _clean_catalog_text(code, field="code")
    plan.name = _clean_catalog_text(name, field="name")
    plan.period_scheme = scheme
    plan.is_active = is_active
    plan.full_clean()
    try:
        plan.save(
            update_fields=[
                "code",
                "name",
                "period_scheme",
                "is_active",
                "updated_at",
            ]
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "A subscription plan with this code already exists."}
        ) from exc
    _replace_plan_allowances_locked(plan=plan, allowances=normalized)
    _audit(
        event_type="SubscriptionPlanChanged",
        aggregate_type="SubscriptionPlan",
        aggregate_id=plan.id,
        actor=actor,
        payload={
            "previous": previous,
            "code": plan.code,
            "name": plan.name,
            "period_scheme_id": (
                str(plan.period_scheme_id) if plan.period_scheme_id else None
            ),
            "is_active": plan.is_active,
            "allowances": normalized,
        },
    )
    return plan


def _policy_has_cases(policy_id: UUID) -> bool:
    return AbsenceCompensationCase.objects.filter(policy_id=policy_id).exists()


def _lock_absence_policy_reasons(*reasons: str) -> None:
    values = sorted({reason for reason in reasons if reason})
    if not values:
        return
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            for reason in values:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    [f"absence-compensation-policy:{reason}"],
                )
    else:
        list(
            AbsenceCompensationPolicy.objects.select_for_update()
            .filter(absence_reason__in=values)
            .values_list("id", flat=True)
        )


@transaction.atomic
def create_absence_compensation_policy(
    *,
    code: str,
    name: str,
    absence_reason: str,
    justification_requirement: str,
    max_eligible_absences: int | None,
    limit_scope: str,
    effective_from: date,
    effective_until: date | None,
    is_active: bool,
    actor: User,
) -> AbsenceCompensationPolicy:
    require_permission(
        actor,
        "subscriptions.add_absencecompensationpolicy",
        "Compensation policy creation permission is required.",
    )
    code = _clean_catalog_text(code, field="code")
    _lock_absence_policy_reasons(absence_reason)
    if AbsenceCompensationPolicy.objects.filter(code=code).exists():
        raise ValidationError(
            {"code": "This policy code already exists. Create a new version instead."}
        )
    policy = AbsenceCompensationPolicy(
        code=code,
        version=1,
        name=_clean_catalog_text(name, field="name"),
        absence_reason=absence_reason,
        justification_requirement=justification_requirement,
        max_eligible_absences=max_eligible_absences,
        limit_scope=limit_scope,
        effective_from=effective_from,
        effective_until=effective_until,
        is_active=is_active,
    )
    policy.full_clean()
    try:
        policy.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "This policy code already exists. Create a new version instead."}
        ) from exc
    _audit(
        event_type="AbsenceCompensationPolicyCreated",
        aggregate_type="AbsenceCompensationPolicy",
        aggregate_id=policy.id,
        actor=actor,
        payload={
            "code": policy.code,
            "version": policy.version,
            "absence_reason": policy.absence_reason,
            "effective_from": policy.effective_from.isoformat(),
            "effective_until": (
                policy.effective_until.isoformat()
                if policy.effective_until is not None
                else None
            ),
            "is_active": policy.is_active,
        },
    )
    return policy


@transaction.atomic
def update_absence_compensation_policy(
    *,
    policy_id: UUID,
    name: str,
    absence_reason: str,
    justification_requirement: str,
    max_eligible_absences: int | None,
    limit_scope: str,
    effective_from: date,
    effective_until: date | None,
    is_active: bool,
    actor: User,
) -> AbsenceCompensationPolicy:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationpolicy",
        "Compensation policy change permission is required.",
    )
    policy_ref = AbsenceCompensationPolicy.objects.only(
        "absence_reason"
    ).get(pk=policy_id)
    _lock_absence_policy_reasons(
        policy_ref.absence_reason,
        absence_reason,
    )
    policy = AbsenceCompensationPolicy.objects.select_for_update().get(
        pk=policy_id
    )
    if policy.absence_reason != policy_ref.absence_reason:
        raise ValidationError(
            {"policy": "Compensation policy changed concurrently. Retry."}
        )
    if _policy_has_cases(policy.id):
        raise ValidationError(
            {"policy": "Referenced policy versions are immutable. Create a new version."}
        )
    previous = {
        "name": policy.name,
        "absence_reason": policy.absence_reason,
        "justification_requirement": policy.justification_requirement,
        "max_eligible_absences": policy.max_eligible_absences,
        "limit_scope": policy.limit_scope,
        "effective_from": policy.effective_from.isoformat(),
        "effective_until": (
            policy.effective_until.isoformat()
            if policy.effective_until is not None
            else None
        ),
        "is_active": policy.is_active,
    }
    policy.name = _clean_catalog_text(name, field="name")
    policy.absence_reason = absence_reason
    policy.justification_requirement = justification_requirement
    policy.max_eligible_absences = max_eligible_absences
    policy.limit_scope = limit_scope
    policy.effective_from = effective_from
    policy.effective_until = effective_until
    policy.is_active = is_active
    policy.full_clean()
    policy.save()
    _audit(
        event_type="AbsenceCompensationPolicyChanged",
        aggregate_type="AbsenceCompensationPolicy",
        aggregate_id=policy.id,
        actor=actor,
        payload={"previous": previous},
    )
    return policy


@transaction.atomic
def version_absence_compensation_policy(
    *,
    policy_id: UUID,
    name: str,
    justification_requirement: str,
    max_eligible_absences: int | None,
    limit_scope: str,
    effective_from: date,
    effective_until: date | None,
    actor: User,
    now=None,
) -> AbsenceCompensationPolicy:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationpolicy",
        "Compensation policy change permission is required.",
    )
    require_permission(
        actor,
        "subscriptions.add_absencecompensationpolicy",
        "Compensation policy creation permission is required.",
    )
    source_ref = AbsenceCompensationPolicy.objects.only(
        "code",
        "absence_reason",
    ).get(pk=policy_id)
    _lock_absence_policy_reasons(source_ref.absence_reason)
    versions = list(
        AbsenceCompensationPolicy.objects.select_for_update()
        .filter(code=source_ref.code)
        .order_by("version", "id")
    )
    source = next(
        (item for item in versions if item.id == policy_id),
        None,
    )
    if source is None:
        raise AbsenceCompensationPolicy.DoesNotExist
    if source.absence_reason != source_ref.absence_reason:
        raise ValidationError(
            {"policy": "Compensation policy changed concurrently. Retry."}
        )
    today = school_date(now or timezone.now())
    if effective_from <= today:
        raise ValidationError(
            {"effective_from": "A new policy version must start after today."}
        )
    if effective_from <= source.effective_from:
        raise ValidationError(
            {"effective_from": "A new version must start after the source version."}
        )
    if effective_until is not None and effective_until < effective_from:
        raise ValidationError(
            {"effective_until": "Effective until cannot precede effective from."}
        )

    latest_version = max(item.version for item in versions)
    if source.version != latest_version:
        raise ValidationError(
            {"policy": "Only the latest policy version can be versioned."}
        )
    next_version = latest_version + 1

    replacement = AbsenceCompensationPolicy(
        code=source.code,
        version=next_version,
        name=_clean_catalog_text(name, field="name"),
        absence_reason=source.absence_reason,
        justification_requirement=justification_requirement,
        max_eligible_absences=max_eligible_absences,
        limit_scope=limit_scope,
        effective_from=effective_from,
        effective_until=effective_until,
        is_active=True,
    )

    source_new_until = effective_from - timedelta(days=1)
    if source.effective_until is None or source.effective_until >= effective_from:
        AbsenceCompensationPolicy.objects.filter(pk=source.id).update(
            effective_until=source_new_until
        )
        source.effective_until = source_new_until

    replacement.full_clean()
    try:
        replacement.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"policy": "Concurrent policy version creation detected. Retry."}
        ) from exc

    source_actions = list(
        AbsenceCompensationPolicyAction.objects.select_for_update()
        .filter(policy=source)
        .order_by("priority", "action_type", "id")
    )
    source_windows = list(
        AbsenceCompensationPolicyWindow.objects.select_for_update()
        .filter(policy_action__policy=source)
        .order_by("policy_action_id", "priority", "source_from", "id")
    )
    action_map = {}
    for action in source_actions:
        copied = AbsenceCompensationPolicyAction.objects.create(
            policy=replacement,
            action_type=action.action_type,
            target_period_rule=action.target_period_rule,
            requirement=action.requirement,
            validity_days=action.validity_days,
            priority=action.priority,
            is_active=action.is_active,
        )
        action_map[action.id] = copied
    for window in source_windows:
        copied_action = action_map[window.policy_action_id]
        AbsenceCompensationPolicyWindow.objects.create(
            policy_action=copied_action,
            name=window.name,
            source_from=window.source_from,
            source_until=window.source_until,
            target_from=window.target_from,
            target_until=window.target_until,
            requirement_override=window.requirement_override,
            priority=window.priority,
            is_active=window.is_active,
        )

    _audit(
        event_type="AbsenceCompensationPolicyVersioned",
        aggregate_type="AbsenceCompensationPolicy",
        aggregate_id=source.id,
        actor=actor,
        payload={
            "replacement_policy_id": str(replacement.id),
            "code": source.code,
            "source_version": source.version,
            "replacement_version": replacement.version,
            "effective_from": replacement.effective_from.isoformat(),
            "source_effective_until": source.effective_until.isoformat(),
        },
    )
    return replacement


@transaction.atomic
def end_absence_compensation_policy(
    *,
    policy_id: UUID,
    inactive_from: date,
    actor: User,
    now=None,
) -> AbsenceCompensationPolicy:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationpolicy",
        "Compensation policy change permission is required.",
    )
    source_ref = AbsenceCompensationPolicy.objects.only(
        "code",
        "absence_reason",
    ).get(pk=policy_id)
    _lock_absence_policy_reasons(source_ref.absence_reason)
    versions = list(
        AbsenceCompensationPolicy.objects.select_for_update()
        .filter(code=source_ref.code)
        .order_by("version", "id")
    )
    policy = next(
        (item for item in versions if item.id == policy_id),
        None,
    )
    if policy is None:
        raise AbsenceCompensationPolicy.DoesNotExist
    if policy.absence_reason != source_ref.absence_reason:
        raise ValidationError(
            {"policy": "Compensation policy changed concurrently. Retry."}
        )
    if policy.version != max(item.version for item in versions):
        raise ValidationError(
            {"policy": "Only the latest policy version can be ended."}
        )
    today = school_date(now or timezone.now())
    if inactive_from <= today:
        raise ValidationError(
            {"inactive_from": "Policy termination must start after today."}
        )
    if inactive_from <= policy.effective_from:
        raise ValidationError(
            {
                "inactive_from": (
                    "Policy termination must start after its effective-from date."
                )
            }
        )
    new_until = inactive_from - timedelta(days=1)
    if (
        policy.effective_until is not None
        and policy.effective_until <= new_until
    ):
        raise ValidationError(
            {
                "inactive_from": (
                    "This policy version already ends on or before that date."
                )
            }
        )

    previous_until = policy.effective_until
    AbsenceCompensationPolicy.objects.filter(pk=policy.id).update(
        effective_until=new_until
    )
    policy.effective_until = new_until
    _audit(
        event_type="AbsenceCompensationPolicyEnded",
        aggregate_type="AbsenceCompensationPolicy",
        aggregate_id=policy.id,
        actor=actor,
        payload={
            "code": policy.code,
            "version": policy.version,
            "inactive_from": inactive_from.isoformat(),
            "effective_until": new_until.isoformat(),
            "previous_effective_until": (
                previous_until.isoformat()
                if previous_until is not None
                else None
            ),
        },
    )
    return policy


def _require_unreferenced_policy(policy: AbsenceCompensationPolicy) -> None:
    if _policy_has_cases(policy.id):
        raise ValidationError(
            {"policy": "Referenced policy versions are immutable. Create a new version."}
        )


@transaction.atomic
def create_absence_compensation_policy_action(
    *,
    policy_id: UUID,
    action_type: str,
    target_period_rule: str,
    requirement: str,
    validity_days: int | None,
    priority: int,
    is_active: bool,
    actor: User,
) -> AbsenceCompensationPolicyAction:
    require_permission(
        actor,
        "subscriptions.add_absencecompensationpolicyaction",
        "Compensation policy action creation permission is required.",
    )
    policy = AbsenceCompensationPolicy.objects.select_for_update().get(
        pk=policy_id
    )
    _require_unreferenced_policy(policy)
    action = AbsenceCompensationPolicyAction(
        policy=policy,
        action_type=action_type,
        target_period_rule=target_period_rule,
        requirement=requirement,
        validity_days=validity_days,
        priority=priority,
        is_active=is_active,
    )
    action.full_clean()
    try:
        action.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"action_type": "This policy already has an action of this type."}
        ) from exc
    _audit(
        event_type="AbsenceCompensationPolicyActionCreated",
        aggregate_type="AbsenceCompensationPolicyAction",
        aggregate_id=action.id,
        actor=actor,
        payload={"policy_id": str(policy.id), "action_type": action.action_type},
    )
    return action


@transaction.atomic
def update_absence_compensation_policy_action(
    *,
    action_id: UUID,
    action_type: str,
    target_period_rule: str,
    requirement: str,
    validity_days: int | None,
    priority: int,
    is_active: bool,
    actor: User,
) -> AbsenceCompensationPolicyAction:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationpolicyaction",
        "Compensation policy action change permission is required.",
    )
    action_ref = AbsenceCompensationPolicyAction.objects.only(
        "policy_id"
    ).get(pk=action_id)
    policy = AbsenceCompensationPolicy.objects.select_for_update().get(
        pk=action_ref.policy_id
    )
    _require_unreferenced_policy(policy)
    action = (
        AbsenceCompensationPolicyAction.objects.select_for_update()
        .select_related("policy")
        .get(pk=action_id)
    )
    action.action_type = action_type
    action.target_period_rule = target_period_rule
    action.requirement = requirement
    action.validity_days = validity_days
    action.priority = priority
    action.is_active = is_active
    action.full_clean()
    try:
        action.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"action_type": "This policy already has an action of this type."}
        ) from exc
    _audit(
        event_type="AbsenceCompensationPolicyActionChanged",
        aggregate_type="AbsenceCompensationPolicyAction",
        aggregate_id=action.id,
        actor=actor,
        payload={"policy_id": str(action.policy_id)},
    )
    return action


@transaction.atomic
def create_absence_compensation_policy_window(
    *,
    action_id: UUID,
    name: str,
    source_from: date,
    source_until: date,
    target_from: date,
    target_until: date,
    requirement_override: str,
    priority: int,
    is_active: bool,
    actor: User,
) -> AbsenceCompensationPolicyWindow:
    require_permission(
        actor,
        "subscriptions.add_absencecompensationpolicywindow",
        "Compensation policy window creation permission is required.",
    )
    action_ref = AbsenceCompensationPolicyAction.objects.only(
        "policy_id"
    ).get(pk=action_id)
    policy = AbsenceCompensationPolicy.objects.select_for_update().get(
        pk=action_ref.policy_id
    )
    _require_unreferenced_policy(policy)
    action = (
        AbsenceCompensationPolicyAction.objects.select_for_update()
        .select_related("policy")
        .get(pk=action_id)
    )
    window = AbsenceCompensationPolicyWindow(
        policy_action=action,
        name=_clean_catalog_text(name, field="name"),
        source_from=source_from,
        source_until=source_until,
        target_from=target_from,
        target_until=target_until,
        requirement_override=requirement_override,
        priority=priority,
        is_active=is_active,
    )
    window.full_clean()
    window.save()
    _audit(
        event_type="AbsenceCompensationPolicyWindowCreated",
        aggregate_type="AbsenceCompensationPolicyWindow",
        aggregate_id=window.id,
        actor=actor,
        payload={
            "policy_id": str(action.policy_id),
            "action_id": str(action.id),
        },
    )
    return window


@transaction.atomic
def update_absence_compensation_policy_window(
    *,
    window_id: UUID,
    name: str,
    source_from: date,
    source_until: date,
    target_from: date,
    target_until: date,
    requirement_override: str,
    priority: int,
    is_active: bool,
    actor: User,
) -> AbsenceCompensationPolicyWindow:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationpolicywindow",
        "Compensation policy window change permission is required.",
    )
    window_ref = (
        AbsenceCompensationPolicyWindow.objects.select_related(
            "policy_action"
        )
        .only("policy_action__policy_id")
        .get(pk=window_id)
    )
    policy = AbsenceCompensationPolicy.objects.select_for_update().get(
        pk=window_ref.policy_action.policy_id
    )
    _require_unreferenced_policy(policy)
    action = AbsenceCompensationPolicyAction.objects.select_for_update().get(
        pk=window_ref.policy_action_id
    )
    window = (
        AbsenceCompensationPolicyWindow.objects.select_for_update()
        .select_related("policy_action__policy")
        .get(pk=window_id)
    )
    window.name = _clean_catalog_text(name, field="name")
    window.source_from = source_from
    window.source_until = source_until
    window.target_from = target_from
    window.target_until = target_until
    window.requirement_override = requirement_override
    window.priority = priority
    window.is_active = is_active
    window.full_clean()
    window.save()
    _audit(
        event_type="AbsenceCompensationPolicyWindowChanged",
        aggregate_type="AbsenceCompensationPolicyWindow",
        aggregate_id=window.id,
        actor=actor,
        payload={
            "policy_id": str(window.policy_action.policy_id),
            "action_id": str(window.policy_action_id),
        },
    )
    return window


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
            status__in=[
                AbsenceCompensationCase.Status.OPEN,
                AbsenceCompensationCase.Status.MATERIALIZED,
            ],
        )
        .first()
    )
    if existing is not None:
        if existing.absence_reason != absence_reason:
            if existing.status == AbsenceCompensationCase.Status.MATERIALIZED:
                message = (
                    "A MATERIALIZED compensation case already exists with "
                    "a different absence reason. Reverse its materialized "
                    "actions before creating a replacement case."
                )
            else:
                message = (
                    "An open compensation case already exists with a "
                    "different absence reason. Cancel it before creating "
                    "a replacement case."
                )
            raise ValidationError({"attendance": message})
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
    policy = AbsenceCompensationPolicy.objects.select_for_update().get(
        pk=policy.id
    )
    current_policy = get_applicable_absence_policy(
        absence_reason=absence_reason,
        source_date=source_date,
        policy_code=policy_code,
    )
    if current_policy is None or current_policy.id != policy.id:
        raise ValidationError(
            {
                "policy": (
                    "Compensation policy changed concurrently. "
                    "Retry case creation."
                )
            }
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
        .filter(
            case_id=case_id,
            action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        )
        .first()
    )
    if existing is not None:
        return existing

    case = (
        AbsenceCompensationCase.objects.select_for_update(of=("self",))
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
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
    )
    if action is None:
        raise ValidationError(
            {"case": "FREE_MAKEUP is not allowed by this case policy."}
        )
    if action.get("requirement") not in (
        None,
        "",
        AbsenceCompensationPolicyAction.Requirement.NONE,
    ):
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

    duplicate_makeup = (
        MakeupEntitlement.objects.select_for_update()
        .filter(
            student_id=case.student_id,
            source_lesson_id=case.source_lesson_id,
            cancelled_at__isnull=True,
            reason__in=[
                MakeupEntitlement.Reason.MEDICAL_VERIFIED,
                MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
            ],
        )
        .first()
    )
    if duplicate_makeup is not None:
        raise ValidationError(
            {
                "case": (
                    "This absence already has an active compensation "
                    "make-up entitlement."
                )
            }
        )

    target_rule = action.get("target_period_rule")
    target_from = action.get("target_from")
    target_until = action.get("target_until")

    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
    ):
        valid_from = case.source_date
        valid_until = subscription.valid_until
    elif (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD
    ):
        raise ValidationError(
            {
                "case": (
                    "NEXT_STUDENT_PERIOD materialization requires the "
                    "subscription-period model and is not implemented yet."
                )
            }
        )
    elif (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
    ):
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
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        action_snapshot=action,
        makeup_entitlement=entitlement,
        activated_at=materialized_at,
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
            "valid_from": (
                valid_from.isoformat() if valid_from is not None else None
            ),
            "valid_until": (
                valid_until.isoformat() if valid_until is not None else None
            ),
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
            "valid_from": (
                valid_from.isoformat() if valid_from is not None else None
            ),
            "valid_until": (
                valid_until.isoformat() if valid_until is not None else None
            ),
            "reason": entitlement.reason,
            "compensation_case_id": str(case.id),
            "compensation_grant_id": str(grant.id),
        },
    )
    return grant


def _paid_makeup_requirement_flags(
    action: dict,
) -> tuple[bool, bool]:
    requirement = action.get("requirement")
    if requirement == AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED:
        return True, False
    if (
        requirement
        == AbsenceCompensationPolicyAction.Requirement.FEE_AND_TARGET_SUBSCRIPTION_REQUIRED
    ):
        return True, True
    raise ValidationError(
        {
            "case": (
                "PAID_MAKEUP requires FEE_REQUIRED or "
                "FEE_AND_TARGET_SUBSCRIPTION_REQUIRED."
            )
        }
    )


def _lock_paid_makeup_target_subscription(
    *,
    case: AbsenceCompensationCase,
    subscription_id: UUID,
) -> Subscription:
    subscription = (
        Subscription.objects.select_for_update(of=("self",))
        .select_related("billing_period")
        .get(pk=subscription_id)
    )
    if subscription.student_id != case.student_id:
        raise ValidationError(
            {"target_subscription": "Target subscription belongs to another student."}
        )
    if subscription.cancelled_at is not None:
        raise ValidationError(
            {"target_subscription": "Target subscription is cancelled."}
        )
    if not subscription.allowances.filter(category=case.category).exists():
        raise ValidationError(
            {
                "target_subscription": (
                    "Target subscription does not include the required "
                    "lesson category."
                )
            }
        )
    return subscription


def _validate_paid_makeup_target(
    *,
    case: AbsenceCompensationCase,
    action: dict,
    source_subscription: Subscription,
    target_subscription: Subscription | None,
) -> tuple[date | None, date | None]:
    target_rule = action.get("target_period_rule")
    target_from = action.get("target_from")
    target_until = action.get("target_until")

    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
    ):
        return case.source_date, source_subscription.valid_until

    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD
    ):
        if target_subscription is None:
            return None, None
        if target_subscription.id == source_subscription.id:
            raise ValidationError(
                {
                    "target_subscription": (
                        "Target subscription must differ from the source "
                        "subscription."
                    )
                }
            )
        if (
            target_subscription.valid_from is None
            or target_subscription.valid_until is None
        ):
            try:
                target_period = target_subscription.billing_period
            except SubscriptionPeriod.DoesNotExist as exc:
                raise ValidationError(
                    {
                        "target_subscription": (
                            "Pending target subscription has no billing "
                            "period metadata."
                        )
                    }
                ) from exc
            if (
                target_period.mode_snapshot
                != SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
                or target_period.state != SubscriptionPeriod.State.PENDING
            ):
                raise ValidationError(
                    {
                        "target_subscription": (
                            "Target subscription has no active dates and is "
                            "not a pending rolling subscription."
                        )
                    }
                )
            if target_period.reference_date <= source_subscription.valid_until:
                raise ValidationError(
                    {
                        "target_subscription": (
                            "Pending rolling target subscription reference "
                            "date must be after the source subscription ends."
                        )
                    }
                )
            return None, None

        if target_subscription.valid_from <= source_subscription.valid_until:
            raise ValidationError(
                {
                    "target_subscription": (
                        "Target subscription for NEXT_STUDENT_PERIOD must "
                        "start after the source subscription ends."
                    )
                }
            )
        return target_subscription.valid_from, target_subscription.valid_until

    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
    ):
        if not target_from or not target_until:
            raise ValidationError({"grant": "Explicit target window is missing."})
        valid_from = date.fromisoformat(target_from)
        valid_until = date.fromisoformat(target_until)
        if target_subscription is not None:
            if (
                target_subscription.valid_from is None
                or target_subscription.valid_until is None
            ):
                raise ValidationError(
                    {
                        "target_subscription": (
                            "Pending target subscription must be activated "
                            "before it can be validated against an explicit "
                            "target window."
                        )
                    }
                )
            valid_from = max(valid_from, target_subscription.valid_from)
            valid_until = min(valid_until, target_subscription.valid_until)
            if valid_until < valid_from:
                raise ValidationError(
                    {
                        "target_subscription": (
                            "Target subscription does not overlap the "
                            "explicit target window."
                        )
                    }
                )
        return valid_from, valid_until

    raise ValidationError({"grant": "Unsupported target-period rule."})


@transaction.atomic
def authorize_paid_makeup_from_case(
    *,
    case_id: UUID,
    actor: User,
    target_subscription_id: UUID | None = None,
    fee_confirmed: bool = False,
    now=None,
) -> AbsenceCompensationActionGrant:
    require_permission(
        actor,
        "subscriptions.add_absencecompensationactiongrant",
        "Compensation action grant permission is required.",
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
        .filter(
            case_id=case_id,
            action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        )
        .first()
    )
    if existing is not None:
        return existing

    case = (
        AbsenceCompensationCase.objects.select_for_update(of=("self",))
        .select_related(
            "attendance",
            "source_subscription_allowance__subscription",
        )
        .get(pk=case_id)
    )
    if case.status != AbsenceCompensationCase.Status.OPEN:
        raise ValidationError(
            {"case": "Only an OPEN compensation case can be authorized."}
        )
    if (
        case.eligibility_status
        != AbsenceCompensationCase.EligibilityStatus.ELIGIBLE
    ):
        raise ValidationError(
            {"case": "Compensation case must be ELIGIBLE before authorization."}
        )
    if case.attendance.status != Attendance.Status.ABSENT:
        raise ValidationError(
            {"attendance": "Source attendance is no longer ABSENT."}
        )

    action = _case_action_snapshot(
        case=case,
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
    )
    if action is None:
        raise ValidationError(
            {"case": "PAID_MAKEUP is not allowed by this case policy."}
        )
    fee_required, target_subscription_required = (
        _paid_makeup_requirement_flags(action)
    )

    allowance = case.source_subscription_allowance
    if allowance is None:
        raise ValidationError(
            {"case": "PAID_MAKEUP requires a source subscription allowance."}
        )
    locked_allowance, balance = locked_allowance_balance(allowance.id)
    allowance = locked_allowance
    source_subscription = Subscription.objects.select_for_update().get(
        pk=allowance.subscription_id
    )
    if source_subscription.cancelled_at is not None:
        raise ValidationError(
            {"case": "Source subscription is cancelled."}
        )
    if balance <= 0:
        raise ValidationError(
            {"case": "Source allowance has no remaining visits."}
        )

    duplicate_makeup = (
        MakeupEntitlement.objects.select_for_update()
        .filter(
            student_id=case.student_id,
            source_lesson_id=case.source_lesson_id,
            cancelled_at__isnull=True,
            reason__in=[
                MakeupEntitlement.Reason.MEDICAL_VERIFIED,
                MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
            ],
        )
        .first()
    )
    if duplicate_makeup is not None:
        raise ValidationError(
            {
                "case": (
                    "This absence already has an active compensation "
                    "make-up entitlement."
                )
            }
        )

    target_subscription = None
    if target_subscription_id is not None:
        target_subscription = _lock_paid_makeup_target_subscription(
            case=case,
            subscription_id=target_subscription_id,
        )

    target_rule = action.get("target_period_rule")
    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD
        and target_subscription is None
    ):
        raise ValidationError(
            {
                "target_subscription": (
                    "NEXT_STUDENT_PERIOD requires an explicit target "
                    "subscription before authorization until the "
                    "subscription-period resolver is implemented."
                )
            }
        )
    if target_subscription is not None:
        _validate_paid_makeup_target(
            case=case,
            action=action,
            source_subscription=source_subscription,
            target_subscription=target_subscription,
        )

    materialized_at = now or timezone.now()
    grant = AbsenceCompensationActionGrant.objects.create(
        case=case,
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        action_snapshot=action,
        target_subscription=target_subscription,
        fee_confirmed_at=materialized_at if fee_confirmed and fee_required else None,
        fee_confirmed_by=actor if fee_confirmed and fee_required else None,
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
        event_type="PaidFreezeAuthorized",
        aggregate_type="AbsenceCompensationActionGrant",
        aggregate_id=grant.id,
        actor=actor,
        correlation_id=correlation_id,
        payload={
            "case_id": str(case.id),
            "target_subscription_id": (
                str(target_subscription.id)
                if target_subscription is not None
                else None
            ),
            "fee_required": fee_required,
            "target_subscription_required": target_subscription_required,
            "fee_confirmed": grant.fee_confirmed_at is not None,
            "eligibility_status": case.eligibility_status,
            "eligible_absence_ordinal": case.eligible_absence_ordinal,
        },
    )
    _audit(
        event_type="AbsenceCompensationMaterialized",
        aggregate_type="AbsenceCompensationCase",
        aggregate_id=case.id,
        actor=actor,
        correlation_id=correlation_id,
        payload={
            "action_type": grant.action_type,
            "grant_id": str(grant.id),
            "makeup_entitlement_id": None,
            "eligibility_status": case.eligibility_status,
            "eligible_absence_ordinal": case.eligible_absence_ordinal,
        },
    )
    return grant


@transaction.atomic
def confirm_paid_makeup_fee(
    *,
    grant_id: UUID,
    actor: User,
    now=None,
) -> AbsenceCompensationActionGrant:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationactiongrant",
        "Compensation action grant change permission is required.",
    )
    grant = AbsenceCompensationActionGrant.objects.select_for_update().get(
        pk=grant_id
    )
    if (
        grant.action_type
        != AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
    ):
        raise ValidationError({"grant": "Grant is not PAID_MAKEUP."})
    if grant.reversed_at is not None:
        raise ValidationError({"grant": "Reversed grant cannot confirm payment."})

    fee_required, _target_required = _paid_makeup_requirement_flags(
        grant.action_snapshot
    )
    if not fee_required:
        raise ValidationError({"grant": "This action does not require a fee."})
    if grant.fee_confirmed_at is not None:
        return grant

    confirmed_at = now or timezone.now()
    grant.fee_confirmed_at = confirmed_at
    grant.fee_confirmed_by = actor
    grant.save(
        update_fields=["fee_confirmed_at", "fee_confirmed_by"]
    )
    _audit(
        event_type="PaidFreezeFeeConfirmed",
        aggregate_type="AbsenceCompensationActionGrant",
        aggregate_id=grant.id,
        actor=actor,
        payload={
            "case_id": str(grant.case_id),
            "confirmed_at": confirmed_at.isoformat(),
        },
    )
    return grant


@transaction.atomic
def activate_paid_makeup_grant(
    *,
    grant_id: UUID,
    actor: User,
    target_subscription_id: UUID | None = None,
    now=None,
) -> AbsenceCompensationActionGrant:
    require_permission(
        actor,
        "subscriptions.add_makeupentitlement",
        "Make-up entitlement permission is required.",
    )
    require_permission(
        actor,
        "subscriptions.change_absencecompensationactiongrant",
        "Compensation action grant change permission is required.",
    )

    grant_ref = AbsenceCompensationActionGrant.objects.only(
        "case_id",
    ).get(pk=grant_id)
    case_ref = AbsenceCompensationCase.objects.only(
        "student_id",
    ).get(pk=grant_ref.case_id)
    Student.objects.select_for_update().get(pk=case_ref.student_id)

    case = (
        AbsenceCompensationCase.objects.select_for_update(of=("self",))
        .select_related(
            "attendance",
            "source_subscription_allowance__subscription",
        )
        .get(pk=grant_ref.case_id)
    )
    grant = AbsenceCompensationActionGrant.objects.select_for_update().get(
        pk=grant_id
    )
    if (
        grant.action_type
        != AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
    ):
        raise ValidationError({"grant": "Grant is not PAID_MAKEUP."})
    if grant.reversed_at is not None:
        raise ValidationError({"grant": "Reversed grant cannot be activated."})
    if grant.activated_at is not None:
        return grant

    if case.status != AbsenceCompensationCase.Status.MATERIALIZED:
        raise ValidationError(
            {"case": "PAID_MAKEUP activation requires a MATERIALIZED case."}
        )
    if case.attendance.status != Attendance.Status.ABSENT:
        raise ValidationError(
            {"attendance": "Source attendance is no longer ABSENT."}
        )

    fee_required, target_subscription_required = (
        _paid_makeup_requirement_flags(grant.action_snapshot)
    )
    if fee_required and grant.fee_confirmed_at is None:
        raise ValidationError(
            {"grant": "Paid make-up fee has not been confirmed."}
        )

    target_subscription = None
    if (
        target_subscription_id is not None
        and grant.target_subscription_id is not None
        and target_subscription_id != grant.target_subscription_id
    ):
        raise ValidationError(
            {
                "target_subscription": (
                    "Target subscription was already fixed when the paid "
                    "make-up was authorized."
                )
            }
        )
    resolved_target_subscription_id = (
        grant.target_subscription_id or target_subscription_id
    )
    if resolved_target_subscription_id is not None:
        target_subscription = _lock_paid_makeup_target_subscription(
            case=case,
            subscription_id=resolved_target_subscription_id,
        )
    if target_subscription_required and target_subscription is None:
        raise ValidationError(
            {"target_subscription": "Target subscription is required."}
        )

    duplicate_makeup = (
        MakeupEntitlement.objects.select_for_update()
        .filter(
            student_id=case.student_id,
            source_lesson_id=case.source_lesson_id,
            cancelled_at__isnull=True,
            reason__in=[
                MakeupEntitlement.Reason.MEDICAL_VERIFIED,
                MakeupEntitlement.Reason.ABSENCE_COMPENSATION,
            ],
        )
        .first()
    )
    if duplicate_makeup is not None:
        raise ValidationError(
            {
                "case": (
                    "This absence already has an active compensation "
                    "make-up entitlement."
                )
            }
        )

    allowance = case.source_subscription_allowance
    if allowance is None:
        raise ValidationError(
            {"case": "PAID_MAKEUP requires a source subscription allowance."}
        )
    locked_allowance, balance = locked_allowance_balance(allowance.id)
    allowance = locked_allowance
    source_subscription = Subscription.objects.select_for_update().get(
        pk=allowance.subscription_id
    )
    if source_subscription.cancelled_at is not None:
        raise ValidationError({"case": "Source subscription is cancelled."})
    if balance <= 0:
        raise ValidationError(
            {"case": "Source allowance has no remaining visits."}
        )

    action = grant.action_snapshot
    valid_from, valid_until = _validate_paid_makeup_target(
        case=case,
        action=action,
        source_subscription=source_subscription,
        target_subscription=target_subscription,
    )
    if valid_from is None or valid_until is None:
        if target_subscription is not None:
            raise ValidationError(
                {
                    "target_subscription": (
                        "Target rolling subscription is still pending "
                        "activation. Activate its billing period before "
                        "activating the paid make-up."
                    )
                }
            )
        raise ValidationError(
            {"target_subscription": "Target subscription is required."}
        )

    validity_days = action.get("validity_days")
    if validity_days is not None:
        bounded_until = valid_from + timedelta(days=int(validity_days) - 1)
        if bounded_until < valid_until:
            valid_until = bounded_until

    activated_at = now or timezone.now()
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
    grant.makeup_entitlement = entitlement
    grant.target_subscription = target_subscription
    grant.activated_at = activated_at
    grant.save(
        update_fields=[
            "makeup_entitlement",
            "target_subscription",
            "activated_at",
        ]
    )

    correlation_id = uuid4()
    _audit(
        event_type="PaidFreezeActivated",
        aggregate_type="AbsenceCompensationActionGrant",
        aggregate_id=grant.id,
        actor=actor,
        correlation_id=correlation_id,
        payload={
            "case_id": str(case.id),
            "makeup_entitlement_id": str(entitlement.id),
            "target_subscription_id": (
                str(target_subscription.id)
                if target_subscription is not None
                else None
            ),
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
            "paid": True,
        },
    )
    return grant


def _reverse_materialized_absence_compensation_cases(
    *,
    attendance_id: UUID,
    actor: User | None,
    at,
    reason: str,
    source_justification_id: UUID | None = None,
    correlation_id: UUID | None = None,
    refund_required: bool | None = None,
    allow_paid_automatic: bool = False,
    skip_if_paid_confirmed: bool = False,
) -> int:
    attendance = Attendance.objects.select_for_update().get(pk=attendance_id)
    Student.objects.select_for_update().get(pk=attendance.student_id)

    cases = AbsenceCompensationCase.objects.select_for_update().filter(
        attendance_id=attendance.id,
        status=AbsenceCompensationCase.Status.MATERIALIZED,
    )
    if source_justification_id is not None:
        cases = cases.filter(source_justification_id=source_justification_id)
    cases = list(cases.order_by("id"))
    if not cases:
        return 0

    case_ids = [case.id for case in cases]
    grants = list(
        AbsenceCompensationActionGrant.objects.select_for_update()
        .filter(
            case_id__in=case_ids,
            reversed_at__isnull=True,
        )
        .order_by("id")
    )
    paid_confirmed = [
        grant
        for grant in grants
        if (
            grant.action_type
            == AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
            and grant.fee_confirmed_at is not None
        )
    ]
    if paid_confirmed and skip_if_paid_confirmed:
        return 0
    if paid_confirmed and not allow_paid_automatic and refund_required is None:
        raise ValidationError(
            {
                "case": (
                    "Paid make-up has a confirmed fee. Use the explicit "
                    "compensation reversal workflow and record whether a "
                    "refund is required."
                ),
                "manager_action_required": (
                    "A manager must decide whether the confirmed paid "
                    "make-up requires a refund before this source change."
                ),
            }
        )
    if paid_confirmed and refund_required is None:
        raise ValidationError(
            {"refund_required": "Refund decision is required for paid reversal."}
        )
    entitlement_ids = [
        grant.makeup_entitlement_id
        for grant in grants
        if grant.makeup_entitlement_id is not None
    ]
    entitlements = {
        item.id: item
        for item in MakeupEntitlement.objects.select_for_update()
        .filter(id__in=entitlement_ids)
        .order_by("id")
    }

    for entitlement_id in entitlement_ids:
        if AttendanceCoverage.objects.filter(
            makeup_entitlement_id=entitlement_id,
            reversed_at__isnull=True,
        ).exists():
            raise ValidationError(
                {
                    "attendance": (
                        "The compensation make-up is already used. Reverse "
                        "or rebind that coverage before changing the source "
                        "absence."
                    )
                }
            )

    effective_correlation_id = correlation_id or uuid4()
    for grant in grants:
        entitlement = (
            entitlements.get(grant.makeup_entitlement_id)
            if grant.makeup_entitlement_id is not None
            else None
        )
        if entitlement is not None and entitlement.cancelled_at is None:
            entitlement.cancelled_at = at
            entitlement.cancelled_by = actor
            entitlement.save(
                update_fields=["cancelled_at", "cancelled_by"]
            )
            _audit(
                event_type="MakeupEntitlementCancelled",
                aggregate_type="MakeupEntitlement",
                aggregate_id=entitlement.id,
                actor=actor,
                correlation_id=effective_correlation_id,
                payload={
                    "attendance_id": str(attendance.id),
                    "compensation_case_id": str(grant.case_id),
                    "cancelled_at": at.isoformat(),
                    "reason": reason,
                },
            )

        grant.reversed_at = at
        grant.reversed_by = actor
        grant.reversal_reason = reason
        if (
            grant.action_type
            == AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
            and grant.fee_confirmed_at is not None
        ):
            grant.refund_required = refund_required
        grant.save(
            update_fields=[
                "reversed_at",
                "reversed_by",
                "reversal_reason",
                "refund_required",
            ]
        )
        _audit(
            event_type="AbsenceCompensationActionReversed",
            aggregate_type="AbsenceCompensationActionGrant",
            aggregate_id=grant.id,
            actor=actor,
            correlation_id=effective_correlation_id,
            payload={
                "case_id": str(grant.case_id),
                "action_type": grant.action_type,
                "reason": reason,
                "reversed_at": at.isoformat(),
                "fee_confirmed": grant.fee_confirmed_at is not None,
                "refund_required": grant.refund_required,
            },
        )
        if (
            grant.action_type
            == AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
        ):
            _audit(
                event_type="PaidFreezeCancelled",
                aggregate_type="AbsenceCompensationActionGrant",
                aggregate_id=grant.id,
                actor=actor,
                correlation_id=effective_correlation_id,
                payload={
                    "case_id": str(grant.case_id),
                    "reason": reason,
                    "reversed_at": at.isoformat(),
                    "activated": grant.activated_at is not None,
                    "fee_confirmed": grant.fee_confirmed_at is not None,
                    "refund_required": grant.refund_required,
                },
            )
            if grant.fee_confirmed_at is not None and grant.refund_required:
                _audit(
                    event_type="PaidFreezeRefundRequired",
                    aggregate_type="AbsenceCompensationActionGrant",
                    aggregate_id=grant.id,
                    actor=actor,
                    correlation_id=effective_correlation_id,
                    payload={
                        "case_id": str(grant.case_id),
                        "reason": reason,
                        "reversed_at": at.isoformat(),
                    },
                )

    for case in cases:
        case.status = AbsenceCompensationCase.Status.REVERSED
        case.reversed_at = at
        case.reversed_by = actor
        case.reversal_reason = reason
        case.save(
            update_fields=[
                "status",
                "reversed_at",
                "reversed_by",
                "reversal_reason",
            ]
        )
        _audit(
            event_type="AbsenceCompensationCaseReversed",
            aggregate_type="AbsenceCompensationCase",
            aggregate_id=case.id,
            actor=actor,
            correlation_id=effective_correlation_id,
            payload={
                "attendance_id": str(attendance.id),
                "reason": reason,
                "reversed_at": at.isoformat(),
            },
        )

    _reevaluate_open_compensation_cases_for_student(
        student_id=attendance.student_id,
        actor=actor,
        evaluated_at=at,
    )
    return len(cases)


@transaction.atomic
def reverse_absence_compensation_case(
    *,
    case_id: UUID,
    actor: User,
    reason: str,
    refund_required: bool | None = None,
    now=None,
) -> AbsenceCompensationCase:
    require_permission(
        actor,
        "subscriptions.change_absencecompensationcase",
        "Absence compensation case change permission is required.",
    )
    reason = reason.strip()
    if not reason:
        raise ValidationError(
            {"reason": "A reversal reason is required."}
        )

    case_ref = AbsenceCompensationCase.objects.only(
        "attendance_id",
        "status",
    ).get(pk=case_id)
    if case_ref.status == AbsenceCompensationCase.Status.REVERSED:
        return case_ref
    if case_ref.status != AbsenceCompensationCase.Status.MATERIALIZED:
        raise ValidationError(
            {
                "case": (
                    "Only a MATERIALIZED compensation case can be "
                    "reversed."
                )
            }
        )

    reversed_at = now or timezone.now()
    _reverse_materialized_absence_compensation_cases(
        attendance_id=case_ref.attendance_id,
        actor=actor,
        at=reversed_at,
        reason=reason,
        refund_required=refund_required,
        allow_paid_automatic=True,
    )
    case_ref.refresh_from_db()
    return case_ref


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


def resolve_subscription_period_window(
    *,
    scheme: SubscriptionPeriodScheme,
    reference_date: date,
    first_lesson_date: date | None = None,
) -> tuple[date, date] | None:
    if scheme.mode == SubscriptionPeriodScheme.Mode.CALENDAR_MONTH:
        starts_on = reference_date.replace(day=1)
        if starts_on.month == 12:
            next_month = date(starts_on.year + 1, 1, 1)
        else:
            next_month = date(
                starts_on.year,
                starts_on.month + 1,
                1,
            )
        return starts_on, next_month - timedelta(days=1)

    if scheme.mode == SubscriptionPeriodScheme.Mode.FIXED_28_DAYS:
        if scheme.fixed_anchor_date is None:
            raise ValidationError(
                {"scheme": "Fixed 28-day period scheme has no anchor date."}
            )
        period_index = (
            (reference_date - scheme.fixed_anchor_date).days // 28
        )
        starts_on = scheme.fixed_anchor_date + timedelta(
            days=period_index * 28
        )
        return starts_on, starts_on + timedelta(days=27)

    if (
        scheme.mode
        == SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
    ):
        if first_lesson_date is None:
            return None
        return first_lesson_date, first_lesson_date + timedelta(days=27)

    raise ValidationError({"scheme": "Unsupported subscription period mode."})


@transaction.atomic
def attach_subscription_period(
    *,
    subscription_id: UUID,
    scheme_id: UUID,
    reference_date: date,
    actor: User,
    now=None,
) -> SubscriptionPeriod:
    require_permission(
        actor,
        "subscriptions.change_subscription",
        "Subscription change permission is required.",
    )
    subscription = Subscription.objects.select_for_update().get(
        pk=subscription_id
    )
    existing = (
        SubscriptionPeriod.objects.select_for_update()
        .filter(subscription=subscription)
        .first()
    )
    if existing is not None:
        return existing

    scheme = SubscriptionPeriodScheme.objects.select_for_update().get(
        pk=scheme_id
    )
    if not scheme.is_active:
        raise ValidationError(
            {"scheme": "Inactive period scheme cannot be assigned."}
        )

    resolved = resolve_subscription_period_window(
        scheme=scheme,
        reference_date=reference_date,
    )
    values = {
        "subscription": subscription,
        "scheme": scheme,
        "mode_snapshot": scheme.mode,
        "fixed_anchor_snapshot": scheme.fixed_anchor_date,
        "reference_date": reference_date,
    }
    if resolved is None:
        period = SubscriptionPeriod.objects.create(**values)
    else:
        starts_on, ends_on = resolved
        period = SubscriptionPeriod.objects.create(
            **values,
            state=SubscriptionPeriod.State.ACTIVE,
            starts_on=starts_on,
            ends_on=ends_on,
            activated_at=now or timezone.now(),
        )

    _audit(
        event_type="SubscriptionPeriodAttached",
        aggregate_type="SubscriptionPeriod",
        aggregate_id=period.id,
        actor=actor,
        payload={
            "subscription_id": str(subscription.id),
            "scheme_id": str(scheme.id),
            "mode": scheme.mode,
            "state": period.state,
            "starts_on": (
                period.starts_on.isoformat()
                if period.starts_on is not None
                else None
            ),
            "ends_on": (
                period.ends_on.isoformat()
                if period.ends_on is not None
                else None
            ),
        },
    )
    return period


@transaction.atomic
def activate_rolling_subscription_period(
    *,
    subscription_id: UUID,
    lesson_id: UUID,
    actor: User | None,
    now=None,
) -> SubscriptionPeriod:
    subscription = Subscription.objects.select_for_update().get(
        pk=subscription_id
    )
    period = (
        SubscriptionPeriod.objects.select_for_update()
        .select_related("scheme")
        .get(subscription=subscription)
    )
    if period.state == SubscriptionPeriod.State.ACTIVE:
        return period
    if (
        period.mode_snapshot
        != SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
    ):
        raise ValidationError(
            {"period": "Only rolling 28-day periods require activation."}
        )

    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    lesson_date = school_date(lesson.starts_at)
    if lesson_date < period.reference_date:
        raise ValidationError(
            {
                "lesson": (
                    "Rolling subscription cannot be activated by a lesson "
                    "before its reference date."
                )
            }
        )
    starts_on, ends_on = resolve_subscription_period_window(
        scheme=period.scheme,
        reference_date=lesson_date,
        first_lesson_date=lesson_date,
    )

    subscription.valid_from = starts_on
    subscription.valid_until = ends_on
    subscription.save(update_fields=["valid_from", "valid_until"])

    period.state = SubscriptionPeriod.State.ACTIVE
    period.starts_on = starts_on
    period.ends_on = ends_on
    period.activation_lesson = lesson
    period.activated_at = now or timezone.now()
    period.save(
        update_fields=[
            "state",
            "starts_on",
            "ends_on",
            "activation_lesson",
            "activated_at",
        ]
    )
    _audit(
        event_type="SubscriptionPeriodActivated",
        aggregate_type="SubscriptionPeriod",
        aggregate_id=period.id,
        actor=actor,
        payload={
            "subscription_id": str(subscription.id),
            "lesson_id": str(lesson.id),
            "starts_on": starts_on.isoformat(),
            "ends_on": ends_on.isoformat(),
        },
    )
    return period


@transaction.atomic
def create_group_place_hold(
    *,
    student_id: UUID,
    group_id: UUID,
    period_scheme_id: UUID,
    period_from: date,
    period_until: date,
    actor: User,
) -> GroupPlaceHold:
    require_permission(
        actor,
        "subscriptions.add_groupplacehold",
        "Group place hold permission is required.",
    )
    student = Student.objects.select_for_update().get(pk=student_id)
    group = TrainingGroup.objects.select_for_update().get(pk=group_id)
    scheme = SubscriptionPeriodScheme.objects.select_for_update().get(
        pk=period_scheme_id
    )
    if not scheme.is_active:
        raise ValidationError(
            {"period_scheme": "Inactive period scheme cannot be used."}
        )

    expected = resolve_subscription_period_window(
        scheme=scheme,
        reference_date=period_from,
        first_lesson_date=(
            period_from
            if (
                scheme.mode
                == SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
            )
            else None
        ),
    )
    if expected != (period_from, period_until):
        raise ValidationError(
            {
                "period": (
                    "Place hold dates must match one full billing period "
                    "for the selected scheme."
                )
            }
        )

    existing = (
        GroupPlaceHold.objects.select_for_update()
        .filter(
            student=student,
            group=group,
            period_from=period_from,
        )
        .exclude(status=GroupPlaceHold.Status.CANCELLED)
        .order_by("created_at", "id")
        .first()
    )
    if existing is not None:
        if (
            existing.period_until != period_until
            or existing.period_scheme_id != scheme.id
        ):
            raise ValidationError(
                {"period": "A conflicting place hold already exists."}
            )
        return existing

    overlapping = (
        GroupPlaceHold.objects.select_for_update()
        .filter(
            student=student,
            group=group,
            period_from__lte=period_until,
            period_until__gte=period_from,
        )
        .exclude(status=GroupPlaceHold.Status.CANCELLED)
        .order_by("period_from", "id")
        .first()
    )
    if overlapping is not None:
        raise ValidationError(
            {
                "period": (
                    "Another non-cancelled place hold overlaps this period "
                    "for the same student and group."
                )
            }
        )

    hold = GroupPlaceHold.objects.create(
        student=student,
        group=group,
        period_scheme=scheme,
        period_from=period_from,
        period_until=period_until,
        created_by=actor,
    )

    _audit(
        event_type="GroupPlaceHoldCreated",
        aggregate_type="GroupPlaceHold",
        aggregate_id=hold.id,
        actor=actor,
        payload={
            "student_id": str(student.id),
            "group_id": str(group.id),
            "period_scheme_id": str(scheme.id),
            "period_from": period_from.isoformat(),
            "period_until": period_until.isoformat(),
        },
    )
    return hold


@transaction.atomic
def confirm_group_place_hold_fee(
    *,
    hold_id: UUID,
    actor: User,
    now=None,
) -> GroupPlaceHold:
    require_permission(
        actor,
        "subscriptions.change_groupplacehold",
        "Group place hold change permission is required.",
    )
    hold = GroupPlaceHold.objects.select_for_update().get(pk=hold_id)
    if hold.status == GroupPlaceHold.Status.ACTIVE:
        return hold
    if hold.status != GroupPlaceHold.Status.PENDING_PAYMENT:
        raise ValidationError(
            {"hold": "Only a pending place hold can confirm payment."}
        )

    confirmed_at = now or timezone.now()
    hold.status = GroupPlaceHold.Status.ACTIVE
    hold.fee_confirmed_at = confirmed_at
    hold.fee_confirmed_by = actor
    hold.save(
        update_fields=[
            "status",
            "fee_confirmed_at",
            "fee_confirmed_by",
        ]
    )
    _audit(
        event_type="GroupPlaceHoldActivated",
        aggregate_type="GroupPlaceHold",
        aggregate_id=hold.id,
        actor=actor,
        payload={"fee_confirmed_at": confirmed_at.isoformat()},
    )
    return hold


@transaction.atomic
def cancel_group_place_hold(
    *,
    hold_id: UUID,
    actor: User,
    reason: str,
    now=None,
) -> GroupPlaceHold:
    require_permission(
        actor,
        "subscriptions.change_groupplacehold",
        "Group place hold change permission is required.",
    )
    reason = reason.strip()
    if not reason:
        raise ValidationError(
            {"reason": "Place hold cancellation reason is required."}
        )
    hold = GroupPlaceHold.objects.select_for_update().get(pk=hold_id)
    if hold.status == GroupPlaceHold.Status.CANCELLED:
        return hold
    if hold.status == GroupPlaceHold.Status.EXPIRED:
        raise ValidationError(
            {"hold": "Expired place hold cannot be cancelled."}
        )

    cancelled_at = now or timezone.now()
    hold.status = GroupPlaceHold.Status.CANCELLED
    hold.cancelled_at = cancelled_at
    hold.cancelled_by = actor
    hold.cancellation_reason = reason
    hold.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
        ]
    )
    _audit(
        event_type="GroupPlaceHoldCancelled",
        aggregate_type="GroupPlaceHold",
        aggregate_id=hold.id,
        actor=actor,
        payload={
            "reason": reason,
            "cancelled_at": cancelled_at.isoformat(),
        },
    )
    return hold


@transaction.atomic
def issue_subscription(
    *,
    student_id: UUID,
    plan_id: UUID,
    valid_from: date | None,
    valid_until: date | None,
    actor: User,
) -> Subscription:
    require_permission(
        actor,
        "subscriptions.add_subscription",
        "Subscription issue permission is required.",
    )
    if (valid_from is None) != (valid_until is None):
        raise ValidationError(
            {
                "valid_until": (
                    "valid_from and valid_until must either both be set "
                    "or both be empty."
                )
            }
        )
    if (
        valid_from is not None
        and valid_until is not None
        and valid_until < valid_from
    ):
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
            "valid_from": (
                valid_from.isoformat() if valid_from is not None else None
            ),
            "valid_until": (
                valid_until.isoformat() if valid_until is not None else None
            ),
            "allowances": issued,
        },
    )
    return subscription


@transaction.atomic
def issue_subscription_for_period(
    *,
    student_id: UUID,
    plan_id: UUID,
    reference_date: date,
    actor: User,
    now=None,
) -> Subscription:
    plan = (
        SubscriptionPlan.objects.select_for_update(of=("self",))
        .select_related("period_scheme")
        .get(pk=plan_id)
    )
    if plan.period_scheme_id is None:
        raise ValidationError(
            {"plan": "Subscription plan has no period scheme."}
        )
    scheme = SubscriptionPeriodScheme.objects.select_for_update().get(
        pk=plan.period_scheme_id
    )
    if not scheme.is_active:
        raise ValidationError(
            {"plan": "Subscription plan period scheme is inactive."}
        )

    resolved = resolve_subscription_period_window(
        scheme=scheme,
        reference_date=reference_date,
    )
    if resolved is None:
        valid_from = None
        valid_until = None
    else:
        valid_from, valid_until = resolved

    subscription = issue_subscription(
        student_id=student_id,
        plan_id=plan.id,
        valid_from=valid_from,
        valid_until=valid_until,
        actor=actor,
    )
    attach_subscription_period(
        subscription_id=subscription.id,
        scheme_id=scheme.id,
        reference_date=reference_date,
        actor=actor,
        now=now,
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


def _activate_pending_rolling_subscription_for_attendance(
    *,
    attendance: Attendance,
    category: str,
    lesson_date: date,
    actor: User | None,
    now=None,
) -> bool:
    period_ids = list(
        SubscriptionPeriod.objects.filter(
            state=SubscriptionPeriod.State.PENDING,
            mode_snapshot=(
                SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
            ),
            subscription__student_id=attendance.student_id,
            subscription__cancelled_at__isnull=True,
            subscription__allowances__category=category,
            reference_date__lte=lesson_date,
        )
        .order_by(
            "subscription__created_at",
            "created_at",
            "id",
        )
        .values_list("id", flat=True)
    )

    for period_id in period_ids:
        period = (
            SubscriptionPeriod.objects.select_for_update()
            .select_related("subscription", "scheme")
            .get(pk=period_id)
        )
        if period.state != SubscriptionPeriod.State.PENDING:
            continue
        if lesson_date < period.reference_date:
            continue

        allowance = (
            SubscriptionAllowance.objects.select_for_update()
            .filter(
                subscription_id=period.subscription_id,
                category=category,
            )
            .first()
        )
        if allowance is None:
            continue
        _locked, balance = locked_allowance_balance(allowance.id)
        if balance <= 0:
            continue

        starts_on, ends_on = resolve_subscription_period_window(
            scheme=period.scheme,
            reference_date=lesson_date,
            first_lesson_date=lesson_date,
        )
        subscription = period.subscription
        subscription.valid_from = starts_on
        subscription.valid_until = ends_on
        subscription.save(update_fields=["valid_from", "valid_until"])

        period.state = SubscriptionPeriod.State.ACTIVE
        period.starts_on = starts_on
        period.ends_on = ends_on
        period.activation_lesson_id = attendance.lesson_id
        period.activated_at = now or timezone.now()
        period.save(
            update_fields=[
                "state",
                "starts_on",
                "ends_on",
                "activation_lesson",
                "activated_at",
            ]
        )
        _audit(
            event_type="SubscriptionPeriodActivated",
            aggregate_type="SubscriptionPeriod",
            aggregate_id=period.id,
            actor=actor,
            payload={
                "subscription_id": str(subscription.id),
                "lesson_id": str(attendance.lesson_id),
                "starts_on": starts_on.isoformat(),
                "ends_on": ends_on.isoformat(),
                "source": "first_covered_lesson",
            },
        )
        return True
    return False


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
    now=None,
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

    coverage = _try_ordinary_allowance_coverage(
        attendance=attendance,
        category=category,
        lesson_date=lesson_date,
        actor=actor,
        correlation_id=correlation_id,
    )
    if coverage is not None:
        return coverage

    activated = _activate_pending_rolling_subscription_for_attendance(
        attendance=attendance,
        category=category,
        lesson_date=lesson_date,
        actor=actor,
        now=now,
    )
    if not activated:
        return None

    return _try_ordinary_allowance_coverage(
        attendance=attendance,
        category=category,
        lesson_date=lesson_date,
        actor=actor,
        correlation_id=correlation_id,
    )


def _rolling_period_revert_dependencies(
    *,
    subscription_id: UUID,
    excluded_coverage_id: UUID,
) -> dict[str, bool]:
    return {
        "active_coverages": AttendanceCoverage.objects.filter(
            subscription_allowance__subscription_id=subscription_id,
            reversed_at__isnull=True,
        )
        .exclude(pk=excluded_coverage_id)
        .exists(),
        "active_makeups": MakeupEntitlement.objects.filter(
            source_subscription_allowance__subscription_id=subscription_id,
            cancelled_at__isnull=True,
        ).exists(),
        "active_compensation_cases": AbsenceCompensationCase.objects.filter(
            source_subscription_allowance__subscription_id=subscription_id,
            status__in=[
                AbsenceCompensationCase.Status.OPEN,
                AbsenceCompensationCase.Status.MATERIALIZED,
            ],
        ).exists(),
        "active_compensation_grants": AbsenceCompensationActionGrant.objects.filter(
            case__source_subscription_allowance__subscription_id=subscription_id,
            reversed_at__isnull=True,
        ).exists(),
    }


def _maybe_revert_rolling_subscription_activation(
    *,
    coverage: AttendanceCoverage,
    subscription_id: UUID,
    period: SubscriptionPeriod | None,
    actor: User | None,
    correlation_id: UUID,
) -> bool:
    if period is None:
        return False
    if (
        period.state != SubscriptionPeriod.State.ACTIVE
        or period.mode_snapshot
        != SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
        or period.activation_lesson_id != coverage.attendance.lesson_id
    ):
        return False

    dependencies = _rolling_period_revert_dependencies(
        subscription_id=subscription_id,
        excluded_coverage_id=coverage.id,
    )
    if dependencies["active_coverages"]:
        _audit(
            event_type="SubscriptionPeriodActivationRevertSkipped",
            aggregate_type="SubscriptionPeriod",
            aggregate_id=period.id,
            actor=actor,
            payload={
                "subscription_id": str(subscription_id),
                "activation_lesson_id": str(period.activation_lesson_id),
                "reversed_coverage_id": str(coverage.id),
                "reason": "active_coverages_remain",
                "dependencies": dependencies,
            },
            correlation_id=correlation_id,
        )
        return False

    if any(
        dependencies[key]
        for key in (
            "active_makeups",
            "active_compensation_cases",
            "active_compensation_grants",
        )
    ):
        _audit(
            event_type="SubscriptionPeriodActivationRevertSkipped",
            aggregate_type="SubscriptionPeriod",
            aggregate_id=period.id,
            actor=actor,
            payload={
                "subscription_id": str(subscription_id),
                "activation_lesson_id": str(period.activation_lesson_id),
                "reversed_coverage_id": str(coverage.id),
                "reason": "dependent_rights_exist",
                "dependencies": dependencies,
            },
            correlation_id=correlation_id,
        )
        return False

    subscription = Subscription.objects.select_for_update().get(
        pk=subscription_id
    )
    previous_starts_on = period.starts_on
    previous_ends_on = period.ends_on
    activation_lesson_id = period.activation_lesson_id

    subscription.valid_from = None
    subscription.valid_until = None
    subscription.save(update_fields=["valid_from", "valid_until"])

    period.state = SubscriptionPeriod.State.PENDING
    period.starts_on = None
    period.ends_on = None
    period.activation_lesson = None
    period.activated_at = None
    period.save(
        update_fields=[
            "state",
            "starts_on",
            "ends_on",
            "activation_lesson",
            "activated_at",
        ]
    )
    _audit(
        event_type="SubscriptionPeriodActivationReverted",
        aggregate_type="SubscriptionPeriod",
        aggregate_id=period.id,
        actor=actor,
        payload={
            "subscription_id": str(subscription.id),
            "activation_lesson_id": str(activation_lesson_id),
            "reverted_coverage_id": str(coverage.id),
            "previous_starts_on": (
                previous_starts_on.isoformat()
                if previous_starts_on is not None
                else None
            ),
            "previous_ends_on": (
                previous_ends_on.isoformat()
                if previous_ends_on is not None
                else None
            ),
            "reason": "attendance_coverage_reversed",
        },
        correlation_id=correlation_id,
    )
    return True

@transaction.atomic
def reverse_attendance_coverage(
    *,
    coverage_id: UUID,
    actor: User | None = None,
    correlation_id: UUID | None = None,
    now=None,
) -> AttendanceCoverage:
    correlation_id = correlation_id or uuid4()
    coverage = (
        AttendanceCoverage.objects.select_for_update()
        .select_related("attendance")
        .get(pk=coverage_id)
    )
    if coverage.reversed_at is not None:
        return coverage

    subscription_id = None
    period = None
    if coverage.subscription_allowance_id is not None:
        allowance_ref = SubscriptionAllowance.objects.only(
            "subscription_id"
        ).get(pk=coverage.subscription_allowance_id)
        subscription_id = allowance_ref.subscription_id
        period = (
            SubscriptionPeriod.objects.select_for_update()
            .filter(subscription_id=subscription_id)
            .first()
        )
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

    coverage.reversed_at = now or timezone.now()
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
    if subscription_id is not None:
        _maybe_revert_rolling_subscription_activation(
            coverage=coverage,
            subscription_id=subscription_id,
            period=period,
            actor=actor,
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
    subscription_ref = Subscription.objects.only(
        "student_id",
    ).get(pk=subscription_id)
    Student.objects.select_for_update().get(pk=subscription_ref.student_id)
    subscription = Subscription.objects.select_for_update().get(
        pk=subscription_id
    )
    if subscription.cancelled_at is not None:
        return subscription

    cancelled_at = at or timezone.now()
    as_of = school_date(cancelled_at)

    pending_target_paid = AbsenceCompensationActionGrant.objects.filter(
        target_subscription_id=subscription.id,
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        reversed_at__isnull=True,
        makeup_entitlement__isnull=True,
    ).exists()
    if pending_target_paid:
        raise ValidationError(
            {
                "subscription": (
                    "Subscription is required by a pending PAID_MAKEUP grant. "
                    "Reverse that compensation grant before cancelling the "
                    "subscription."
                )
            }
        )

    pending_source_paid = AbsenceCompensationActionGrant.objects.filter(
        case__source_subscription_allowance__subscription_id=subscription.id,
        action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
        reversed_at__isnull=True,
        makeup_entitlement__isnull=True,
    ).exists()
    if pending_source_paid:
        raise ValidationError(
            {
                "subscription": (
                    "Subscription funds a pending PAID_MAKEUP grant. "
                    "Reverse that compensation grant before cancelling the "
                    "subscription."
                )
            }
        )

    usable_source_makeups = usable_makeups_for_subscription(
        subscription_id=subscription.id,
        as_of=as_of,
    )
    if usable_source_makeups:
        raise ValidationError(
            {
                "subscription": (
                    "Subscription funds an unused, unexpired make-up "
                    "entitlement. Reverse or cancel that make-up right "
                    "before cancelling the subscription."
                )
            }
        )

    active_target_makeup = (
        AbsenceCompensationActionGrant.objects.filter(
            target_subscription_id=subscription.id,
            action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
            reversed_at__isnull=True,
            makeup_entitlement__cancelled_at__isnull=True,
            makeup_entitlement__valid_until__gte=as_of,
        )
        .annotate(
            is_used=Exists(
                AttendanceCoverage.objects.filter(
                    makeup_entitlement_id=OuterRef("makeup_entitlement_id"),
                    reversed_at__isnull=True,
                )
            )
        )
        .filter(is_used=False)
        .exists()
    )
    if active_target_makeup:
        raise ValidationError(
            {
                "subscription": (
                    "Subscription is required by an unused, unexpired "
                    "PAID_MAKEUP entitlement. Reverse that compensation "
                    "grant before cancelling the subscription."
                )
            }
        )

    list(
        SubscriptionAllowance.objects.select_for_update()
        .filter(subscription_id=subscription.id)
        .order_by("id")
        .values_list("id", flat=True)
    )

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


def _paid_makeup_authorization_deadline(
    grant: AbsenceCompensationActionGrant,
) -> tuple[date | None, str | None]:
    source_subscription = (
        grant.case.source_subscription_allowance.subscription
    )
    target_rule = grant.action_snapshot.get("target_period_rule")

    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD
    ):
        return source_subscription.valid_until, None

    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD
    ):
        if grant.target_subscription is None:
            return None, "missing_target_subscription"
        if grant.target_subscription.valid_until is not None:
            return grant.target_subscription.valid_until, None
        try:
            target_period = grant.target_subscription.billing_period
        except SubscriptionPeriod.DoesNotExist:
            return None, "target_subscription_missing_period"
        if (
            target_period.state != SubscriptionPeriod.State.PENDING
            or target_period.mode_snapshot
            != SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON
        ):
            return None, "target_subscription_pending_activation"
        target_window = resolve_subscription_period_window(
            scheme=target_period.scheme,
            reference_date=target_period.reference_date,
            first_lesson_date=target_period.reference_date,
        )
        if target_window is None:
            return None, "target_subscription_deadline_unresolved"
        _target_from, target_until = target_window
        return target_until, None

    if (
        target_rule
        == AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW
    ):
        target_until = grant.action_snapshot.get("target_until")
        if not target_until:
            return None, "missing_explicit_target_until"
        try:
            return date.fromisoformat(target_until), None
        except (TypeError, ValueError):
            return None, "invalid_explicit_target_until"

    return None, "unsupported_target_period_rule"


@transaction.atomic
def _process_one_paid_makeup_authorization_expiry(
    *,
    grant_id: UUID,
    as_of: date,
    actor: User | None,
) -> int:
    grant = (
        AbsenceCompensationActionGrant.objects.select_related(
            "case",
            "case__source_subscription_allowance__subscription",
            "target_subscription",
            "target_subscription__billing_period__scheme",
        )
        .get(pk=grant_id)
    )
    if (
        grant.action_type
        != AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
        or grant.reversed_at is not None
        or grant.activated_at is not None
        or grant.fee_confirmed_at is not None
        or grant.case.status != AbsenceCompensationCase.Status.MATERIALIZED
    ):
        return 0

    deadline, deadline_issue = _paid_makeup_authorization_deadline(grant)
    if deadline_issue is not None:
        if not event_exists(
            event_type="PaidFreezeAuthorizationDeadlineUnresolved",
            aggregate_type="AbsenceCompensationActionGrant",
            aggregate_id=grant.id,
        ):
            _audit(
                event_type="PaidFreezeAuthorizationDeadlineUnresolved",
                aggregate_type="AbsenceCompensationActionGrant",
                aggregate_id=grant.id,
                actor=actor,
                payload={
                    "case_id": str(grant.case_id),
                    "target_period_rule": grant.action_snapshot.get(
                        "target_period_rule"
                    ),
                    "target_subscription_id": (
                        str(grant.target_subscription_id)
                        if grant.target_subscription_id is not None
                        else None
                    ),
                    "issue": deadline_issue,
                    "as_of": as_of.isoformat(),
                },
            )
        return 0
    if deadline >= as_of:
        return 0

    reversed_at = timezone.now()
    count = _reverse_materialized_absence_compensation_cases(
        attendance_id=grant.case.attendance_id,
        actor=actor,
        at=reversed_at,
        reason="authorization_expired",
        allow_paid_automatic=True,
        skip_if_paid_confirmed=True,
    )
    if count:
        _audit(
            event_type="PaidFreezeAuthorizationExpired",
            aggregate_type="AbsenceCompensationActionGrant",
            aggregate_id=grant.id,
            actor=actor,
            payload={
                "case_id": str(grant.case_id),
                "deadline": deadline.isoformat(),
                "as_of": as_of.isoformat(),
            },
        )
        return 1
    return 0


@transaction.atomic
def _process_one_group_place_hold_expiry(
    *,
    hold_id: UUID,
    as_of: date,
    actor: User | None,
) -> int:
    hold = GroupPlaceHold.objects.select_for_update().get(pk=hold_id)
    if (
        hold.status != GroupPlaceHold.Status.ACTIVE
        or hold.period_until >= as_of
    ):
        return 0

    hold.status = GroupPlaceHold.Status.EXPIRED
    hold.save(update_fields=["status"])
    _audit(
        event_type="GroupPlaceHoldExpired",
        aggregate_type="GroupPlaceHold",
        aggregate_id=hold.id,
        actor=actor,
        payload={
            "student_id": str(hold.student_id),
            "group_id": str(hold.group_id),
            "period_from": hold.period_from.isoformat(),
            "period_until": hold.period_until.isoformat(),
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
        "paid_authorization_expired": 0,
        "group_place_hold_expired": 0,
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

    pending_paid_ids = list(
        AbsenceCompensationActionGrant.objects.filter(
            action_type=AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
            reversed_at__isnull=True,
            activated_at__isnull=True,
            fee_confirmed_at__isnull=True,
            case__status=AbsenceCompensationCase.Status.MATERIALIZED,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    for grant_id in pending_paid_ids:
        counts[
            "paid_authorization_expired"
        ] += _process_one_paid_makeup_authorization_expiry(
            grant_id=grant_id,
            as_of=as_of,
            actor=actor,
        )

    active_hold_ids = list(
        GroupPlaceHold.objects.filter(
            status=GroupPlaceHold.Status.ACTIVE,
            period_until__lt=as_of,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    for hold_id in active_hold_ids:
        counts[
            "group_place_hold_expired"
        ] += _process_one_group_place_hold_expiry(
            hold_id=hold_id,
            as_of=as_of,
            actor=actor,
        )

    return counts
