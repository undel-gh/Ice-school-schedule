from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.permissions import (
    require_manager_operations_access,
    require_permission,
)
from core.presentation import validation_message

from .manager_forms import (
    ManagerCompensationCaseCreateForm,
    ManagerAdministrativeMakeupForm,
    ManagerCompensationReverseForm,
    ManagerOneTimeEntitlementForm,
    ManagerPaidMakeupActivateForm,
    ManagerPaidMakeupAuthorizeForm,
)
from .models import (
    AbsenceCompensationActionGrant,
    AbsenceCompensationCase,
    AbsenceCompensationPolicyAction,
    OneTimeEntitlement,
)
from .services import (
    activate_paid_makeup_grant,
    authorize_paid_makeup_from_case,
    cancel_absence_compensation_case,
    cancel_one_time_entitlement,
    confirm_paid_makeup_fee,
    create_absence_compensation_case,
    grant_administrative_makeup,
    grant_one_time_entitlement,
    materialize_free_makeup_from_case,
    reverse_absence_compensation_case,
)


@login_required
def manager_operations_dashboard(request: HttpRequest) -> HttpResponse:
    require_manager_operations_access(request.user)
    return render(request, "subscriptions/manager_operations_dashboard.html")


@login_required
def manager_compensation_cases(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_absencecompensationcase",
        "Compensation case view permission is required.",
    )
    cases = (
        AbsenceCompensationCase.objects.select_related(
            "student",
            "source_lesson__lesson_type",
            "source_subscription_allowance__subscription",
            "policy",
        )
        .prefetch_related("action_grants")
        .order_by("-created_at", "id")
    )
    status = request.GET.get("status")
    if status in AbsenceCompensationCase.Status.values:
        cases = cases.filter(status=status)
    return render(
        request,
        "subscriptions/manager_compensation_cases.html",
        {
            "cases": cases[:300],
            "statuses": AbsenceCompensationCase.Status.choices,
            "selected_status": status or "",
        },
    )


@login_required
def manager_compensation_case_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_absencecompensationcase",
        "Compensation case creation permission is required.",
    )
    form = ManagerCompensationCaseCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            case = create_absence_compensation_case(
                attendance_id=form.cleaned_data["attendance"].id,
                absence_reason=form.cleaned_data["absence_reason"],
                actor=request.user,
                policy_code=form.cleaned_data["policy_code"] or None,
                now=timezone.now(),
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Компенсационный случай создан.")
            return redirect(
                "subscriptions:manager_compensation_case_detail",
                case_id=case.id,
            )
    return render(
        request,
        "subscriptions/manager_compensation_case_form.html",
        {"form": form},
    )


@login_required
def manager_compensation_case_detail(
    request: HttpRequest,
    *,
    case_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_absencecompensationcase",
        "Compensation case view permission is required.",
    )
    case = get_object_or_404(
        AbsenceCompensationCase.objects.select_related(
            "student",
            "attendance",
            "source_lesson__lesson_type",
            "source_subscription_allowance__subscription",
            "source_justification",
            "policy",
        ).prefetch_related(
            "action_grants__makeup_entitlement",
            "action_grants__target_subscription",
        ),
        pk=case_id,
    )
    grants = tuple(case.action_grants.all())
    action_types = {
        item.get("action_type")
        for item in case.actions_snapshot
        if isinstance(item, dict)
    }
    return render(
        request,
        "subscriptions/manager_compensation_case_detail.html",
        {
            "case": case,
            "grants": grants,
            "authorize_form": ManagerPaidMakeupAuthorizeForm(
                student_id=case.student_id
            ),
            "activate_form": ManagerPaidMakeupActivateForm(
                student_id=case.student_id
            ),
            "reverse_form": ManagerCompensationReverseForm(),
            "free_action_type": (
                AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP
            ),
            "paid_action_type": (
                AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
            ),
            "can_materialize_free": (
                AbsenceCompensationPolicyAction.ActionType.FREE_MAKEUP
                in action_types
            ),
            "can_authorize_paid": (
                AbsenceCompensationPolicyAction.ActionType.PAID_MAKEUP
                in action_types
            ),
        },
    )


@login_required
@require_POST
def manager_compensation_materialize_free(
    request: HttpRequest,
    *,
    case_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_makeupentitlement",
        "Make-up entitlement permission is required.",
    )
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationcase",
        "Compensation case change permission is required.",
    )
    get_object_or_404(AbsenceCompensationCase, pk=case_id)
    try:
        materialize_free_makeup_from_case(
            case_id=case_id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Бесплатная отработка выдана.")
    return redirect(
        "subscriptions:manager_compensation_case_detail",
        case_id=case_id,
    )


@login_required
@require_POST
def manager_compensation_authorize_paid(
    request: HttpRequest,
    *,
    case_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_absencecompensationactiongrant",
        "Compensation action grant permission is required.",
    )
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationcase",
        "Compensation case change permission is required.",
    )
    case = get_object_or_404(AbsenceCompensationCase, pk=case_id)
    form = ManagerPaidMakeupAuthorizeForm(
        request.POST,
        student_id=case.student_id,
    )
    if form.is_valid():
        target = form.cleaned_data["target_subscription"]
        try:
            authorize_paid_makeup_from_case(
                case_id=case.id,
                actor=request.user,
                target_subscription_id=target.id if target else None,
                fee_confirmed=form.cleaned_data["fee_confirmed"],
                now=timezone.now(),
            )
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            messages.success(request, "Платная отработка авторизована.")
    else:
        messages.error(request, "Проверьте целевой абонемент.")
    return redirect(
        "subscriptions:manager_compensation_case_detail",
        case_id=case.id,
    )


@login_required
@require_POST
def manager_compensation_confirm_fee(
    request: HttpRequest,
    *,
    grant_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationactiongrant",
        "Compensation action grant change permission is required.",
    )
    grant = get_object_or_404(AbsenceCompensationActionGrant, pk=grant_id)
    try:
        confirm_paid_makeup_fee(
            grant_id=grant.id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Оплата подтверждена.")
    return redirect(
        "subscriptions:manager_compensation_case_detail",
        case_id=grant.case_id,
    )


@login_required
@require_POST
def manager_compensation_activate_paid(
    request: HttpRequest,
    *,
    grant_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_makeupentitlement",
        "Make-up entitlement permission is required.",
    )
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationactiongrant",
        "Compensation action grant change permission is required.",
    )
    grant = get_object_or_404(
        AbsenceCompensationActionGrant.objects.select_related("case"),
        pk=grant_id,
    )
    form = ManagerPaidMakeupActivateForm(
        request.POST,
        student_id=grant.case.student_id,
    )
    if form.is_valid():
        target = form.cleaned_data["target_subscription"]
        try:
            activate_paid_makeup_grant(
                grant_id=grant.id,
                actor=request.user,
                target_subscription_id=target.id if target else None,
                now=timezone.now(),
            )
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            messages.success(request, "Платная отработка активирована.")
    else:
        messages.error(request, "Проверьте целевой абонемент.")
    return redirect(
        "subscriptions:manager_compensation_case_detail",
        case_id=grant.case_id,
    )


@login_required
@require_POST
def manager_compensation_reverse(
    request: HttpRequest,
    *,
    case_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationcase",
        "Compensation case change permission is required.",
    )
    get_object_or_404(AbsenceCompensationCase, pk=case_id)
    form = ManagerCompensationReverseForm(request.POST)
    if form.is_valid():
        try:
            reverse_absence_compensation_case(
                case_id=case_id,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                refund_required=form.refund_value(),
                now=timezone.now(),
            )
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            messages.success(request, "Компенсационный случай отменён.")
    else:
        messages.error(request, "Укажите причину отмены.")
    return redirect(
        "subscriptions:manager_compensation_case_detail",
        case_id=case_id,
    )


@login_required
@require_POST
def manager_compensation_cancel(
    request: HttpRequest,
    *,
    case_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_absencecompensationcase",
        "Compensation case change permission is required.",
    )
    get_object_or_404(AbsenceCompensationCase, pk=case_id)
    try:
        cancel_absence_compensation_case(
            case_id=case_id,
            actor=request.user,
            at=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Открытый компенсационный случай отменён.")
    return redirect(
        "subscriptions:manager_compensation_case_detail",
        case_id=case_id,
    )


@login_required
def manager_one_time_entitlements(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_onetimeentitlement",
        "One-time entitlement view permission is required.",
    )
    entitlements = (
        OneTimeEntitlement.objects.select_related(
            "student", "lesson__lesson_type", "lesson__group"
        )
        .order_by("-created_at", "id")[:300]
    )
    return render(
        request,
        "subscriptions/manager_one_time_entitlements.html",
        {"entitlements": entitlements},
    )


@login_required
def manager_one_time_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_onetimeentitlement",
        "One-time entitlement grant permission is required.",
    )
    form = ManagerOneTimeEntitlementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            entitlement = grant_one_time_entitlement(
                student_id=form.cleaned_data["student"].id,
                lesson_id=form.cleaned_data["lesson"].id,
                entitlement_type=form.cleaned_data["entitlement_type"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Разовое право выдано.")
            return redirect("subscriptions:manager_one_time_entitlements")
    return render(
        request,
        "subscriptions/manager_one_time_form.html",
        {"form": form},
    )


@login_required
@require_POST
def manager_one_time_cancel(
    request: HttpRequest,
    *,
    entitlement_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_onetimeentitlement",
        "One-time entitlement change permission is required.",
    )
    get_object_or_404(OneTimeEntitlement, pk=entitlement_id)
    try:
        cancel_one_time_entitlement(
            entitlement_id=entitlement_id,
            actor=request.user,
            at=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Разовое право отменено.")
    return redirect("subscriptions:manager_one_time_entitlements")


@login_required
def manager_administrative_makeup_create(
    request: HttpRequest,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_makeupentitlement",
        "Make-up entitlement permission is required.",
    )
    form = ManagerAdministrativeMakeupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        target = form.cleaned_data["target_lesson"]
        try:
            grant_administrative_makeup(
                source_subscription_allowance_id=(
                    form.cleaned_data["source_subscription_allowance"].id
                ),
                source_lesson_id=form.cleaned_data["source_lesson"].id,
                valid_from=form.cleaned_data["valid_from"],
                valid_until=form.cleaned_data["valid_until"],
                actor=request.user,
                reason=form.cleaned_data["reason"],
                target_lesson_id=target.id if target else None,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Административная отработка выдана.")
            return redirect("subscriptions:manager_compensation_cases")
    return render(
        request,
        "subscriptions/manager_administrative_makeup_form.html",
        {"form": form},
    )
