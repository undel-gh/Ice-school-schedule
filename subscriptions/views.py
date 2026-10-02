from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Student
from audit.models import AuditEvent
from core.permissions import require_permission
from core.time import school_date
from subscriptions.forms import (
    ManagerAllowanceAdjustmentForm,
    ManagerPlaceHoldCancelForm,
    ManagerPlaceHoldCreateForm,
    ManagerMakeupCancelForm,
    ManagerSubscriptionCancelForm,
    ManagerSubscriptionIssueForm,
)
from subscriptions.models import (
    GroupPlaceHold,
    MakeupEntitlement,
    Subscription,
    SubscriptionAllowance,
)
from subscriptions.selectors import (
    manager_subscription_detail,
    manager_subscription_report,
)
from subscriptions.services import (
    adjust_allowance,
    cancel_group_place_hold,
    cancel_manager_makeup_entitlement,
    cancel_subscription,
    confirm_group_place_hold_fee,
    create_group_place_hold,
    restore_group_place_hold,
    issue_subscription_for_period,
    recover_rolling_subscription_period_activation,
    resolve_subscription_period_window,
)

MAX_MANAGER_REPORT_RANGE_DAYS = 366


def _parse_date(value: str | None, *, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Http404("Invalid date.") from exc


@login_required
def manager_subscription_report_view(
    request: HttpRequest,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_subscription",
        "Manager subscription report permission is required.",
    )

    today = school_date(timezone.now())
    default_from = today.replace(day=1)
    if default_from.month == 12:
        next_month = date(default_from.year + 1, 1, 1)
    else:
        next_month = date(
            default_from.year,
            default_from.month + 1,
            1,
        )
    default_until = next_month - timedelta(days=1)

    from_date = _parse_date(
        request.GET.get("from"),
        default=default_from,
    )
    until_date = _parse_date(
        request.GET.get("until"),
        default=default_until,
    )
    if until_date < from_date:
        raise Http404("Invalid date range.")
    if (until_date - from_date).days > MAX_MANAGER_REPORT_RANGE_DAYS:
        raise Http404("Date range is too large.")

    student_id = None
    raw_student = request.GET.get("student")
    if raw_student:
        try:
            student_id = UUID(raw_student)
        except ValueError as exc:
            raise Http404("Invalid student.") from exc
        if not Student.objects.filter(pk=student_id).exists():
            raise Http404("Student not found.")

    rows = manager_subscription_report(
        as_of=today,
        student_id=student_id,
        from_date=from_date,
        until_date=until_date,
    )
    students = Student.objects.filter(is_active=True).order_by(
        "display_name",
        "id",
    )

    return render(
        request,
        "subscriptions/manager_subscription_report.html",
        {
            "rows": rows,
            "students": students,
            "selected_student_id": (
                str(student_id) if student_id is not None else ""
            ),
            "from_date": from_date,
            "until_date": until_date,
            "as_of": today,
        },
    )



@login_required
def manager_subscription_issue_view(
    request: HttpRequest,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_subscription",
        "Subscription issue permission is required.",
    )
    if request.method == "POST":
        form = ManagerSubscriptionIssueForm(request.POST)
        if form.is_valid():
            try:
                subscription = issue_subscription_for_period(
                    student_id=form.cleaned_data["student"].id,
                    plan_id=form.cleaned_data["plan"].id,
                    reference_date=form.cleaned_data["reference_date"],
                    actor=request.user,
                    now=timezone.now(),
                )
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                messages.success(
                    request,
                    (
                        "Абонемент выдан: "
                        f"{subscription.plan_name_snapshot}."
                    ),
                )
                return redirect(
                    "subscriptions:manager_subscription_report"
                )
    else:
        form = ManagerSubscriptionIssueForm(
            initial={"reference_date": school_date(timezone.now())}
        )

    return render(
        request,
        "subscriptions/manager_subscription_issue.html",
        {"form": form},
    )


@login_required
def manager_place_holds_view(
    request: HttpRequest,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_groupplacehold",
        "Group place hold permission is required.",
    )
    holds = (
        GroupPlaceHold.objects.select_related(
            "student",
            "group",
            "period_scheme",
            "seat_reservation",
            "suspended_membership",
            "restored_membership",
        )
        .order_by("-period_from", "student__display_name", "id")
    )
    return render(
        request,
        "subscriptions/manager_place_holds.html",
        {
            "holds": holds,
            "cancel_form": ManagerPlaceHoldCancelForm(),
        },
    )


@login_required
def manager_place_hold_create_view(
    request: HttpRequest,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.add_groupplacehold",
        "Group place hold permission is required.",
    )
    if request.method == "POST":
        form = ManagerPlaceHoldCreateForm(request.POST)
        if form.is_valid():
            scheme = form.cleaned_data["period_scheme"]
            reference_date = form.cleaned_data["reference_date"]
            resolved = resolve_subscription_period_window(
                scheme=scheme,
                reference_date=reference_date,
                first_lesson_date=(
                    reference_date
                    if scheme.mode
                    == scheme.Mode.ROLLING_28_FROM_FIRST_LESSON
                    else None
                ),
            )
            if resolved is None:
                form.add_error(
                    "reference_date",
                    "Не удалось определить расчётный период.",
                )
            else:
                period_from, period_until = resolved
                try:
                    hold = create_group_place_hold(
                        student_id=form.cleaned_data["student"].id,
                        group_id=form.cleaned_data["group"].id,
                        period_scheme_id=scheme.id,
                        period_from=period_from,
                        period_until=period_until,
                        actor=request.user,
                    )
                except ValidationError as exc:
                    form.add_error(None, " ".join(exc.messages))
                else:
                    messages.success(
                        request,
                        (
                            "Сохранение места создано на период "
                            f"{hold.period_from:%d.%m.%Y}—"
                            f"{hold.period_until:%d.%m.%Y}."
                        ),
                    )
                    return redirect(
                        "subscriptions:manager_place_holds"
                    )
    else:
        form = ManagerPlaceHoldCreateForm(
            initial={"reference_date": school_date(timezone.now())}
        )

    return render(
        request,
        "subscriptions/manager_place_hold_create.html",
        {"form": form},
    )


@login_required
@require_POST
def manager_place_hold_confirm_view(
    request: HttpRequest,
    *,
    hold_id: UUID,
) -> HttpResponse:
    try:
        confirm_group_place_hold_fee(
            hold_id=hold_id,
            actor=request.user,
            now=timezone.now(),
        )
    except GroupPlaceHold.DoesNotExist as exc:
        raise Http404("Place hold not found.") from exc
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Оплата сохранения места подтверждена.")
    return redirect("subscriptions:manager_place_holds")


@login_required
@require_POST
def manager_place_hold_restore_view(
    request: HttpRequest,
    *,
    hold_id: UUID,
) -> HttpResponse:
    try:
        hold = restore_group_place_hold(
            hold_id=hold_id,
            actor=request.user,
            now=timezone.now(),
        )
    except GroupPlaceHold.DoesNotExist as exc:
        raise Http404("Place hold not found.") from exc
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(
            request,
            (
                "Возврат в группу запланирован с "
                f"{hold.restored_membership.starts_on:%d.%m.%Y}."
            ),
        )
    return redirect("subscriptions:manager_place_holds")


@login_required
@require_POST
def manager_place_hold_cancel_view(
    request: HttpRequest,
    *,
    hold_id: UUID,
) -> HttpResponse:
    form = ManagerPlaceHoldCancelForm(request.POST)
    if form.is_valid():
        try:
            cancel_group_place_hold(
                hold_id=hold_id,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                now=timezone.now(),
            )
        except GroupPlaceHold.DoesNotExist as exc:
            raise Http404("Place hold not found.") from exc
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Сохранение места отменено.")
    else:
        messages.error(request, "Укажите причину отмены.")
    return redirect("subscriptions:manager_place_holds")



@login_required
def manager_subscription_detail_view(
    request: HttpRequest,
    *,
    subscription_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_subscription",
        "Subscription view permission is required.",
    )
    try:
        detail = manager_subscription_detail(
            subscription_id=subscription_id,
            as_of=school_date(timezone.now()),
        )
    except (Subscription.DoesNotExist, StopIteration) as exc:
        raise Http404("Subscription not found.") from exc

    period = detail.row.period
    rolling_recovery_event = None
    if period is not None:
        latest_recovery_event = (
            AuditEvent.objects.filter(
                aggregate_type="SubscriptionPeriod",
                aggregate_id=period.id,
                event_type__in=[
                    "SubscriptionPeriodActivationRevertSkipped",
                    "SubscriptionPeriodActivationReverted",
                ],
            )
            .order_by("-occurred_at", "-id")
            .first()
        )
        if (
            latest_recovery_event is not None
            and latest_recovery_event.event_type
            == "SubscriptionPeriodActivationRevertSkipped"
        ):
            rolling_recovery_event = latest_recovery_event

    return render(
        request,
        "subscriptions/manager_subscription_detail.html",
        {
            "detail": detail,
            "adjustment_form": ManagerAllowanceAdjustmentForm(),
            "cancel_form": ManagerSubscriptionCancelForm(),
            "rolling_recovery_event": rolling_recovery_event,
        },
    )


@login_required
@require_POST
def manager_allowance_adjust_view(
    request: HttpRequest,
    *,
    allowance_id: UUID,
) -> HttpResponse:
    allowance = get_object_or_404(
        SubscriptionAllowance,
        pk=allowance_id,
    )
    form = ManagerAllowanceAdjustmentForm(request.POST)
    if form.is_valid():
        try:
            adjust_allowance(
                allowance_id=allowance.id,
                delta=form.cleaned_data["delta"],
                reason=form.cleaned_data["reason"],
                actor=request.user,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Остаток абонемента скорректирован.")
    else:
        messages.error(request, "Проверьте значение и причину корректировки.")
    return redirect(
        "subscriptions:manager_subscription_detail",
        subscription_id=allowance.subscription_id,
    )


@login_required
@require_POST
def manager_makeup_cancel_view(
    request: HttpRequest,
    *,
    makeup_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_makeupentitlement",
        "Make-up entitlement change permission is required.",
    )
    makeup = get_object_or_404(
        MakeupEntitlement.objects.select_related(
            "source_subscription_allowance",
        ),
        pk=makeup_id,
    )
    subscription_id = makeup.source_subscription_allowance.subscription_id
    form = ManagerMakeupCancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Укажите причину отмены права на отработку.")
    else:
        try:
            cancel_manager_makeup_entitlement(
                makeup_entitlement_id=makeup.id,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                now=timezone.now(),
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Право на отработку отменено.")
    return redirect(
        "subscriptions:manager_subscription_detail",
        subscription_id=subscription_id,
    )


@login_required
@require_POST
def manager_subscription_rolling_recovery_view(
    request: HttpRequest,
    *,
    subscription_id: UUID,
) -> HttpResponse:
    try:
        recover_rolling_subscription_period_activation(
            subscription_id=subscription_id,
            actor=request.user,
        )
    except Subscription.DoesNotExist as exc:
        raise Http404("Subscription not found.") from exc
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(
            request,
            "Расчётный период возвращён в ожидание первого занятия.",
        )
    return redirect(
        "subscriptions:manager_subscription_detail",
        subscription_id=subscription_id,
    )


@login_required
@require_POST
def manager_subscription_cancel_view(
    request: HttpRequest,
    *,
    subscription_id: UUID,
) -> HttpResponse:
    form = ManagerSubscriptionCancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Подтвердите отмену абонемента.")
        return redirect(
            "subscriptions:manager_subscription_detail",
            subscription_id=subscription_id,
        )
    try:
        subscription = cancel_subscription(
            subscription_id=subscription_id,
            actor=request.user,
            at=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Абонемент отменён.")
    return redirect(
        "subscriptions:manager_subscription_detail",
        subscription_id=subscription_id,
    )
