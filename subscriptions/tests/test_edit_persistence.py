from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.choices import SubscriptionCategory
from subscriptions.models import (
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
    SubscriptionPeriodScheme,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)


User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="edit-persistence-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


def _referenced_scheme(*, suffix: str = "main") -> SubscriptionPeriodScheme:
    scheme = SubscriptionPeriodScheme.objects.create(
        code=f"period-{suffix}",
        name=f"Period {suffix}",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    SubscriptionPlan.objects.create(
        code=f"plan-{suffix}",
        name=f"Plan {suffix}",
        period_scheme=scheme,
        is_active=True,
    )
    return scheme


@pytest.mark.django_db
def test_manager_period_scheme_edit_persists_allowed_fields(client, manager):
    scheme = _referenced_scheme(suffix="manager")
    client.force_login(manager)

    response = client.post(
        reverse(
            "subscriptions:manager_period_scheme_edit",
            kwargs={"scheme_id": scheme.id},
        ),
        {
            "code": "period-manager-renamed",
            "name": "Переименованный период",
            "mode": SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
            "fixed_anchor_date": "",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    scheme.refresh_from_db()
    assert scheme.code == "period-manager-renamed"
    assert scheme.name == "Переименованный период"
    assert scheme.mode == SubscriptionPeriodScheme.Mode.CALENDAR_MONTH
    assert scheme.is_active is True


@pytest.mark.django_db
def test_django_admin_period_scheme_edit_persists_allowed_fields(client, manager):
    scheme = _referenced_scheme(suffix="admin")
    client.force_login(manager)

    response = client.post(
        reverse(
            "admin:subscriptions_subscriptionperiodscheme_change",
            args=[scheme.id],
        ),
        {
            "code": "period-admin-renamed",
            "name": "Период через Django Admin",
            "mode": SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
            "fixed_anchor_date": "",
            "is_active": "on",
            "_save": "Сохранить",
        },
    )

    assert response.status_code == 302
    scheme.refresh_from_db()
    assert scheme.code == "period-admin-renamed"
    assert scheme.name == "Период через Django Admin"
    assert scheme.mode == SubscriptionPeriodScheme.Mode.CALENDAR_MONTH
    assert scheme.is_active is True


@pytest.mark.django_db
def test_django_admin_period_scheme_rejects_immutable_mode_with_visible_error(
    client,
    manager,
):
    scheme = _referenced_scheme(suffix="immutable")
    client.force_login(manager)

    response = client.post(
        reverse(
            "admin:subscriptions_subscriptionperiodscheme_change",
            args=[scheme.id],
        ),
        {
            "code": scheme.code,
            "name": scheme.name,
            "mode": SubscriptionPeriodScheme.Mode.ROLLING_28_FROM_FIRST_LESSON,
            "fixed_anchor_date": "",
            "is_active": "on",
            "_save": "Сохранить",
        },
    )

    assert response.status_code == 200
    scheme.refresh_from_db()
    assert scheme.mode == SubscriptionPeriodScheme.Mode.CALENDAR_MONTH
    body = response.content.decode().lower()
    assert "используемую модель расчётного периода нельзя менять" in body


@pytest.mark.django_db
def test_manager_plan_edit_persists_plan_and_allowances(client, manager):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="plan-period",
        name="Plan period",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    plan = SubscriptionPlan.objects.create(
        code="ice-8",
        name="8 льдов",
        period_scheme=scheme,
        is_active=True,
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category=SubscriptionCategory.ICE,
        visit_limit=8,
    )
    client.force_login(manager)

    response = client.post(
        reverse("subscriptions:manager_plan_edit", kwargs={"plan_id": plan.id}),
        {
            "code": "ice-12-hall-4",
            "name": "12 льдов + 4 зала",
            "period_scheme": str(scheme.id),
            "ice_visit_limit": "12",
            "hall_visit_limit": "4",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    plan.refresh_from_db()
    assert plan.code == "ice-12-hall-4"
    assert plan.name == "12 льдов + 4 зала"
    assert plan.period_scheme_id == scheme.id
    allowances = {
        item.category: item.visit_limit
        for item in plan.allowances.all()
    }
    assert allowances == {
        SubscriptionCategory.ICE: 12,
        SubscriptionCategory.HALL: 4,
    }


@pytest.mark.django_db
def test_manager_unreferenced_policy_edit_persists(client, manager):
    policy = AbsenceCompensationPolicy.objects.create(
        code="policy-edit",
        version=1,
        name="До изменения",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.UNEXCUSED,
        justification_requirement=AbsenceCompensationPolicy.JustificationRequirement.NONE,
        max_eligible_absences=None,
        limit_scope=AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD,
        effective_from=date(2027, 1, 1),
        effective_until=None,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "subscriptions:manager_policy_edit",
            kwargs={"policy_id": policy.id},
        ),
        {
            "code": policy.code,
            "name": "После изменения",
            "absence_reason": AbsenceCompensationPolicy.AbsenceReason.OTHER,
            "justification_requirement": AbsenceCompensationPolicy.JustificationRequirement.NONE,
            "max_eligible_absences": "3",
            "limit_scope": AbsenceCompensationPolicy.LimitScope.CATEGORY_PERIOD,
            "effective_from": "2027-01-01",
            "effective_until": "2027-12-31",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    policy.refresh_from_db()
    assert policy.name == "После изменения"
    assert policy.absence_reason == AbsenceCompensationPolicy.AbsenceReason.OTHER
    assert policy.max_eligible_absences == 3
    assert policy.limit_scope == AbsenceCompensationPolicy.LimitScope.CATEGORY_PERIOD
    assert policy.effective_until == date(2027, 12, 31)


@pytest.mark.django_db
def test_manager_policy_action_edit_persists(client, manager):
    policy = AbsenceCompensationPolicy.objects.create(
        code="policy-action-edit",
        version=1,
        name="Policy",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.OTHER,
        justification_requirement=AbsenceCompensationPolicy.JustificationRequirement.NONE,
        limit_scope=AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD,
        effective_from=date(2027, 1, 1),
        is_active=True,
    )
    action = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=AbsenceCompensationPolicyAction.TargetPeriodRule.CURRENT_PERIOD,
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
        priority=100,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "subscriptions:manager_policy_action_edit",
            kwargs={"action_id": action.id},
        ),
        {
            "action_type": AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP,
            "target_period_rule": AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD,
            "requirement": AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED,
            "validity_days": "30",
            "priority": "20",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    action.refresh_from_db()
    assert action.action_type == AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
    assert action.target_period_rule == AbsenceCompensationPolicyAction.TargetPeriodRule.NEXT_STUDENT_PERIOD
    assert action.requirement == AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED
    assert action.validity_days == 30
    assert action.priority == 20


@pytest.mark.django_db
def test_manager_policy_window_edit_persists(client, manager):
    policy = AbsenceCompensationPolicy.objects.create(
        code="policy-window-edit",
        version=1,
        name="Policy",
        absence_reason=AbsenceCompensationPolicy.AbsenceReason.OTHER,
        justification_requirement=AbsenceCompensationPolicy.JustificationRequirement.NONE,
        limit_scope=AbsenceCompensationPolicy.LimitScope.STUDENT_PERIOD,
        effective_from=date(2027, 1, 1),
        is_active=True,
    )
    action = AbsenceCompensationPolicyAction.objects.create(
        policy=policy,
        action_type=AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP,
        target_period_rule=AbsenceCompensationPolicyAction.TargetPeriodRule.EXPLICIT_TARGET_WINDOW,
        requirement=AbsenceCompensationPolicyAction.Requirement.NONE,
        priority=100,
        is_active=True,
    )
    window = AbsenceCompensationPolicyWindow.objects.create(
        policy_action=action,
        name="До изменения",
        source_from=date(2027, 5, 1),
        source_until=date(2027, 5, 31),
        target_from=date(2027, 6, 1),
        target_until=date(2027, 6, 30),
        requirement_override="",
        priority=100,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "subscriptions:manager_policy_window_edit",
            kwargs={"window_id": window.id},
        ),
        {
            "name": "После изменения",
            "source_from": "2027-05-01",
            "source_until": "2027-05-31",
            "target_from": "2027-07-01",
            "target_until": "2027-07-31",
            "requirement_override": AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED,
            "priority": "10",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    window.refresh_from_db()
    assert window.name == "После изменения"
    assert window.target_from == date(2027, 7, 1)
    assert window.target_until == date(2027, 7, 31)
    assert window.requirement_override == AbsenceCompensationPolicyAction.Requirement.FEE_REQUIRED
    assert window.priority == 10
