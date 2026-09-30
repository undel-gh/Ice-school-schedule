from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.choices import SubscriptionCategory
from core.permissions import require_manager_operations_access, require_permission
from core.presentation import validation_message
from core.time import school_date

from .catalog_forms import (
    ManagerCompensationPolicyActionForm,
    ManagerCompensationPolicyEndForm,
    ManagerCompensationPolicyForm,
    ManagerCompensationPolicyVersionForm,
    ManagerCompensationPolicyWindowForm,
    ManagerPeriodSchemeForm,
    ManagerSubscriptionPlanForm,
)
from .models import (
    AbsenceCompensationPolicy,
    AbsenceCompensationPolicyAction,
    AbsenceCompensationPolicyWindow,
    SubscriptionPeriodScheme,
    SubscriptionPlan,
)
from .services import (
    create_absence_compensation_policy,
    create_absence_compensation_policy_action,
    create_absence_compensation_policy_window,
    create_subscription_period_scheme,
    create_subscription_plan,
    end_absence_compensation_policy,
    update_absence_compensation_policy,
    update_absence_compensation_policy_action,
    update_absence_compensation_policy_window,
    update_subscription_period_scheme,
    update_subscription_plan,
    version_absence_compensation_policy,
)


@login_required
def manager_catalog(request: HttpRequest) -> HttpResponse:
    require_manager_operations_access(request.user)
    return render(request, "subscriptions/catalog_dashboard.html")


@login_required
def manager_period_schemes(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_subscriptionperiodscheme",
        "Subscription period scheme view permission is required.",
    )
    schemes = SubscriptionPeriodScheme.objects.order_by(
        "-is_active", "name", "id"
    )
    return render(
        request,
        "subscriptions/catalog_period_schemes.html",
        {"schemes": schemes},
    )


@login_required
def manager_period_scheme_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_subscriptionperiodscheme",
        "Subscription period scheme creation permission is required.",
    )
    form = ManagerPeriodSchemeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            scheme = create_subscription_period_scheme(
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                mode=form.cleaned_data["mode"],
                fixed_anchor_date=form.cleaned_data["fixed_anchor_date"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Модель расчётного периода создана.")
            return redirect(
                "subscriptions:manager_period_scheme_edit",
                scheme_id=scheme.id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Новая модель расчётного периода"},
    )


@login_required
def manager_period_scheme_edit(
    request: HttpRequest,
    *,
    scheme_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_subscriptionperiodscheme",
        "Subscription period scheme change permission is required.",
    )
    scheme = get_object_or_404(SubscriptionPeriodScheme, pk=scheme_id)
    form = ManagerPeriodSchemeForm(
        request.POST or None,
        initial={
            "code": scheme.code,
            "name": scheme.name,
            "mode": scheme.mode,
            "fixed_anchor_date": scheme.fixed_anchor_date,
            "is_active": scheme.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_subscription_period_scheme(
                scheme_id=scheme.id,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                mode=form.cleaned_data["mode"],
                fixed_anchor_date=form.cleaned_data["fixed_anchor_date"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Модель расчётного периода обновлена.")
            return redirect("subscriptions:manager_period_schemes")
    return render(
        request,
        "subscriptions/catalog_form.html",
        {
            "form": form,
            "title": "Редактирование модели расчётного периода",
            "object": scheme,
        },
    )


@login_required
def manager_plans(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_subscriptionplan",
        "Subscription plan view permission is required.",
    )
    plans = (
        SubscriptionPlan.objects.select_related("period_scheme")
        .prefetch_related("allowances")
        .order_by("-is_active", "name", "id")
    )
    return render(
        request,
        "subscriptions/catalog_plans.html",
        {"plans": plans},
    )


def _plan_initial(plan: SubscriptionPlan) -> dict:
    allowances = {
        item.category: item.visit_limit
        for item in plan.allowances.all()
    }
    return {
        "code": plan.code,
        "name": plan.name,
        "period_scheme": plan.period_scheme_id,
        "ice_visit_limit": allowances.get(SubscriptionCategory.ICE),
        "hall_visit_limit": allowances.get(SubscriptionCategory.HALL),
        "is_active": plan.is_active,
    }


@login_required
def manager_plan_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_subscriptionplan",
        "Subscription plan creation permission is required.",
    )
    form = ManagerSubscriptionPlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        scheme = form.cleaned_data["period_scheme"]
        try:
            plan = create_subscription_plan(
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                period_scheme_id=scheme.id if scheme else None,
                is_active=form.cleaned_data["is_active"],
                allowances=form.allowances(),
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Тариф создан.")
            return redirect(
                "subscriptions:manager_plan_edit",
                plan_id=plan.id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Новый тариф"},
    )


@login_required
def manager_plan_edit(
    request: HttpRequest,
    *,
    plan_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_subscriptionplan",
        "Subscription plan change permission is required.",
    )
    plan = get_object_or_404(
        SubscriptionPlan.objects.select_related("period_scheme").prefetch_related(
            "allowances"
        ),
        pk=plan_id,
    )
    form = ManagerSubscriptionPlanForm(
        request.POST or None,
        current_scheme_id=plan.period_scheme_id,
        initial=_plan_initial(plan),
    )
    if request.method == "POST" and form.is_valid():
        scheme = form.cleaned_data["period_scheme"]
        try:
            update_subscription_plan(
                plan_id=plan.id,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                period_scheme_id=scheme.id if scheme else None,
                is_active=form.cleaned_data["is_active"],
                allowances=form.allowances(),
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Тариф обновлён.")
            return redirect("subscriptions:manager_plans")
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Редактирование тарифа", "object": plan},
    )


@login_required
def manager_policies(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_absencecompensationpolicy",
        "Compensation policy view permission is required.",
    )
    policies = AbsenceCompensationPolicy.objects.order_by(
        "absence_reason", "code", "-version", "id"
    )
    return render(
        request,
        "subscriptions/catalog_policies.html",
        {"policies": policies},
    )


@login_required
def manager_policy_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_absencecompensationpolicy",
        "Compensation policy creation permission is required.",
    )
    form = ManagerCompensationPolicyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            policy = create_absence_compensation_policy(
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                absence_reason=form.cleaned_data["absence_reason"],
                justification_requirement=form.cleaned_data[
                    "justification_requirement"
                ],
                max_eligible_absences=form.cleaned_data[
                    "max_eligible_absences"
                ],
                limit_scope=form.cleaned_data["limit_scope"],
                effective_from=form.cleaned_data["effective_from"],
                effective_until=form.cleaned_data["effective_until"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Политика компенсаций создана.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=policy.id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Новая политика компенсаций"},
    )


@login_required
def manager_policy_detail(
    request: HttpRequest,
    *,
    policy_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_absencecompensationpolicy",
        "Compensation policy view permission is required.",
    )
    policy = get_object_or_404(
        AbsenceCompensationPolicy.objects.prefetch_related(
            "actions__windows"
        ),
        pk=policy_id,
    )
    actions = list(policy.actions.all().order_by("priority", "action_type", "id"))
    return render(
        request,
        "subscriptions/catalog_policy_detail.html",
        {
            "policy": policy,
            "actions": actions,
            "referenced": policy.compensation_cases.exists(),
            "is_latest": not AbsenceCompensationPolicy.objects.filter(
                code=policy.code,
                version__gt=policy.version,
            ).exists(),
        },
    )


@login_required
def manager_policy_edit(
    request: HttpRequest,
    *,
    policy_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationpolicy",
        "Compensation policy change permission is required.",
    )
    policy = get_object_or_404(AbsenceCompensationPolicy, pk=policy_id)
    if policy.compensation_cases.exists():
        messages.error(
            request,
            "Использованная версия неизменяема. Создайте новую версию.",
        )
        return redirect(
            "subscriptions:manager_policy_detail",
            policy_id=policy.id,
        )
    initial = {
        "code": policy.code,
        "name": policy.name,
        "absence_reason": policy.absence_reason,
        "justification_requirement": policy.justification_requirement,
        "max_eligible_absences": policy.max_eligible_absences,
        "limit_scope": policy.limit_scope,
        "effective_from": policy.effective_from,
        "effective_until": policy.effective_until,
        "is_active": policy.is_active,
    }
    form = ManagerCompensationPolicyForm(
        request.POST or None,
        initial=initial,
    )
    form.fields["code"].disabled = True
    if request.method == "POST" and form.is_valid():
        try:
            update_absence_compensation_policy(
                policy_id=policy.id,
                name=form.cleaned_data["name"],
                absence_reason=form.cleaned_data["absence_reason"],
                justification_requirement=form.cleaned_data[
                    "justification_requirement"
                ],
                max_eligible_absences=form.cleaned_data[
                    "max_eligible_absences"
                ],
                limit_scope=form.cleaned_data["limit_scope"],
                effective_from=form.cleaned_data["effective_from"],
                effective_until=form.cleaned_data["effective_until"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Политика компенсаций обновлена.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=policy.id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {
            "form": form,
            "title": "Редактирование политики компенсаций",
            "object": policy,
        },
    )


@login_required
def manager_policy_version(
    request: HttpRequest,
    *,
    policy_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationpolicy",
        "Compensation policy change permission is required.",
    )
    require_permission(
        request.user,
        "subscriptions.add_absencecompensationpolicy",
        "Compensation policy creation permission is required.",
    )
    policy = get_object_or_404(AbsenceCompensationPolicy, pk=policy_id)
    if AbsenceCompensationPolicy.objects.filter(
        code=policy.code,
        version__gt=policy.version,
    ).exists():
        messages.error(request, "Версионировать можно только последнюю версию.")
        return redirect(
            "subscriptions:manager_policy_detail",
            policy_id=policy.id,
        )
    form = ManagerCompensationPolicyVersionForm(
        request.POST or None,
        initial={
            "name": policy.name,
            "justification_requirement": policy.justification_requirement,
            "max_eligible_absences": policy.max_eligible_absences,
            "limit_scope": policy.limit_scope,
            "effective_from": school_date(timezone.now()) + timedelta(days=1),
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            replacement = version_absence_compensation_policy(
                policy_id=policy.id,
                name=form.cleaned_data["name"],
                justification_requirement=form.cleaned_data[
                    "justification_requirement"
                ],
                max_eligible_absences=form.cleaned_data[
                    "max_eligible_absences"
                ],
                limit_scope=form.cleaned_data["limit_scope"],
                effective_from=form.cleaned_data["effective_from"],
                effective_until=form.cleaned_data["effective_until"],
                actor=request.user,
                now=timezone.now(),
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Создана новая версия политики.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=replacement.id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {
            "form": form,
            "title": f"Новая версия {policy.code} v{policy.version}",
            "object": policy,
        },
    )


@login_required
def manager_policy_end(
    request: HttpRequest,
    *,
    policy_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationpolicy",
        "Compensation policy change permission is required.",
    )
    policy = get_object_or_404(AbsenceCompensationPolicy, pk=policy_id)
    if AbsenceCompensationPolicy.objects.filter(
        code=policy.code,
        version__gt=policy.version,
    ).exists():
        messages.error(request, "Завершить можно только последнюю версию.")
        return redirect(
            "subscriptions:manager_policy_detail",
            policy_id=policy.id,
        )

    today = school_date(timezone.now())
    form = ManagerCompensationPolicyEndForm(
        request.POST or None,
        initial={"inactive_from": today + timedelta(days=1)},
    )
    if request.method == "POST" and form.is_valid():
        try:
            end_absence_compensation_policy(
                policy_id=policy.id,
                inactive_from=form.cleaned_data["inactive_from"],
                actor=request.user,
                now=timezone.now(),
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Политика завершена.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=policy.id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {
            "form": form,
            "title": f"Завершить {policy.code} v{policy.version}",
            "object": policy,
        },
    )


@login_required
def manager_policy_action_create(
    request: HttpRequest,
    *,
    policy_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_absencecompensationpolicyaction",
        "Compensation policy action creation permission is required.",
    )
    policy = get_object_or_404(AbsenceCompensationPolicy, pk=policy_id)
    if policy.compensation_cases.exists():
        messages.error(request, "Использованная версия политики неизменяема.")
        return redirect(
            "subscriptions:manager_policy_detail",
            policy_id=policy.id,
        )
    form = ManagerCompensationPolicyActionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_absence_compensation_policy_action(
                policy_id=policy.id,
                action_type=form.cleaned_data["action_type"],
                target_period_rule=form.cleaned_data["target_period_rule"],
                requirement=form.cleaned_data["requirement"],
                validity_days=form.cleaned_data["validity_days"],
                priority=form.cleaned_data["priority"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Действие политики добавлено.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=policy.id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Новое действие политики", "object": policy},
    )


@login_required
def manager_policy_action_edit(
    request: HttpRequest,
    *,
    action_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationpolicyaction",
        "Compensation policy action change permission is required.",
    )
    action = get_object_or_404(
        AbsenceCompensationPolicyAction.objects.select_related("policy"),
        pk=action_id,
    )
    if action.policy.compensation_cases.exists():
        messages.error(request, "Использованная версия политики неизменяема.")
        return redirect(
            "subscriptions:manager_policy_detail",
            policy_id=action.policy_id,
        )
    form = ManagerCompensationPolicyActionForm(
        request.POST or None,
        initial={
            "action_type": action.action_type,
            "target_period_rule": action.target_period_rule,
            "requirement": action.requirement,
            "validity_days": action.validity_days,
            "priority": action.priority,
            "is_active": action.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_absence_compensation_policy_action(
                action_id=action.id,
                action_type=form.cleaned_data["action_type"],
                target_period_rule=form.cleaned_data["target_period_rule"],
                requirement=form.cleaned_data["requirement"],
                validity_days=form.cleaned_data["validity_days"],
                priority=form.cleaned_data["priority"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Действие политики обновлено.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=action.policy_id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Редактирование действия", "object": action},
    )


@login_required
def manager_policy_window_create(
    request: HttpRequest,
    *,
    action_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_absencecompensationpolicywindow",
        "Compensation policy window creation permission is required.",
    )
    action = get_object_or_404(
        AbsenceCompensationPolicyAction.objects.select_related("policy"),
        pk=action_id,
    )
    if action.policy.compensation_cases.exists():
        messages.error(request, "Использованная версия политики неизменяема.")
        return redirect(
            "subscriptions:manager_policy_detail",
            policy_id=action.policy_id,
        )
    form = ManagerCompensationPolicyWindowForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_absence_compensation_policy_window(
                action_id=action.id,
                name=form.cleaned_data["name"],
                source_from=form.cleaned_data["source_from"],
                source_until=form.cleaned_data["source_until"],
                target_from=form.cleaned_data["target_from"],
                target_until=form.cleaned_data["target_until"],
                requirement_override=form.cleaned_data[
                    "requirement_override"
                ],
                priority=form.cleaned_data["priority"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Сезонное окно добавлено.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=action.policy_id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Новое сезонное окно", "object": action},
    )


@login_required
def manager_policy_window_edit(
    request: HttpRequest,
    *,
    window_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationpolicywindow",
        "Compensation policy window change permission is required.",
    )
    window = get_object_or_404(
        AbsenceCompensationPolicyWindow.objects.select_related(
            "policy_action__policy"
        ),
        pk=window_id,
    )
    if window.policy_action.policy.compensation_cases.exists():
        messages.error(request, "Использованная версия политики неизменяема.")
        return redirect(
            "subscriptions:manager_policy_detail",
            policy_id=window.policy_action.policy_id,
        )
    form = ManagerCompensationPolicyWindowForm(
        request.POST or None,
        initial={
            "name": window.name,
            "source_from": window.source_from,
            "source_until": window.source_until,
            "target_from": window.target_from,
            "target_until": window.target_until,
            "requirement_override": window.requirement_override,
            "priority": window.priority,
            "is_active": window.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_absence_compensation_policy_window(
                window_id=window.id,
                name=form.cleaned_data["name"],
                source_from=form.cleaned_data["source_from"],
                source_until=form.cleaned_data["source_until"],
                target_from=form.cleaned_data["target_from"],
                target_until=form.cleaned_data["target_until"],
                requirement_override=form.cleaned_data[
                    "requirement_override"
                ],
                priority=form.cleaned_data["priority"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Сезонное окно обновлено.")
            return redirect(
                "subscriptions:manager_policy_detail",
                policy_id=window.policy_action.policy_id,
            )
    return render(
        request,
        "subscriptions/catalog_form.html",
        {"form": form, "title": "Редактирование сезонного окна", "object": window},
    )
