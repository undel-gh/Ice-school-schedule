from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Student
from core.permissions import require_permission
from core.presentation import validation_message
from core.time import school_date
from django.utils import timezone

from .models import GroupMembership, TrainingGroup
from .school_admin_forms import GroupMembershipForm, TrainingGroupForm
from .services import (
    create_group_membership,
    create_training_group,
    update_group_membership,
    update_training_group,
)


@login_required
def manager_groups(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_traininggroup",
        "Training group view permission is required.",
    )
    query = request.GET.get("q", "").strip()
    groups = TrainingGroup.objects.order_by("-is_active", "name", "id")
    if query:
        groups = groups.filter(Q(name__icontains=query) | Q(code__icontains=query))
    return render(
        request,
        "scheduling/manager_groups.html",
        {"groups": groups[:300], "query": query},
    )


@login_required
def manager_group_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.add_traininggroup",
        "Training group creation permission is required.",
    )
    form = TrainingGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            group = create_training_group(
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                default_minimum_attendees=form.cleaned_data[
                    "default_minimum_attendees"
                ],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Группа создана.")
            return redirect("school_scheduling:group_detail", group_id=group.id)
    return render(
        request,
        "scheduling/manager_group_form.html",
        {"form": form, "title": "Новая группа"},
    )


@login_required
def manager_group_detail(
    request: HttpRequest,
    *,
    group_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_traininggroup",
        "Training group view permission is required.",
    )
    group = get_object_or_404(TrainingGroup, pk=group_id)
    memberships = group.memberships.select_related("student").order_by(
        "-starts_on", "student__display_name", "id"
    )
    return render(
        request,
        "scheduling/manager_group_detail.html",
        {"group": group, "memberships": memberships},
    )


@login_required
def manager_group_edit(
    request: HttpRequest,
    *,
    group_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.change_traininggroup",
        "Training group change permission is required.",
    )
    group = get_object_or_404(TrainingGroup, pk=group_id)
    form = TrainingGroupForm(
        request.POST or None,
        initial={
            "code": group.code,
            "name": group.name,
            "default_minimum_attendees": group.default_minimum_attendees,
            "is_active": group.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_training_group(
                group_id=group.id,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                default_minimum_attendees=form.cleaned_data[
                    "default_minimum_attendees"
                ],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Группа обновлена.")
            return redirect("school_scheduling:group_detail", group_id=group.id)
    return render(
        request,
        "scheduling/manager_group_form.html",
        {"form": form, "title": "Редактирование группы", "group": group},
    )


@login_required
def manager_memberships(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_groupmembership",
        "Group membership view permission is required.",
    )
    today = school_date(timezone.now())
    query = request.GET.get("q", "").strip()
    state = request.GET.get("state", "current")
    memberships = GroupMembership.objects.select_related(
        "student", "group", "created_by"
    ).order_by("-starts_on", "student__display_name", "id")
    if query:
        memberships = memberships.filter(
            Q(student__display_name__icontains=query)
            | Q(group__name__icontains=query)
            | Q(group__code__icontains=query)
        )
    if state == "current":
        memberships = memberships.filter(starts_on__lte=today).filter(
            Q(ends_on__isnull=True) | Q(ends_on__gte=today)
        )
    elif state == "future":
        memberships = memberships.filter(starts_on__gt=today)
    elif state == "ended":
        memberships = memberships.filter(ends_on__lt=today)
    elif state != "all":
        state = "current"
    return render(
        request,
        "scheduling/manager_memberships.html",
        {
            "memberships": memberships[:500],
            "query": query,
            "state": state,
        },
    )


@login_required
def manager_membership_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.add_groupmembership",
        "Group membership creation permission is required.",
    )
    initial = {}
    student_id = request.GET.get("student")
    group_id = request.GET.get("group")
    if student_id:
        initial["student"] = student_id
    if group_id:
        initial["group"] = group_id
    form = GroupMembershipForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            membership = create_group_membership(
                student_id=form.cleaned_data["student"].id,
                group_id=form.cleaned_data["group"].id,
                starts_on=form.cleaned_data["starts_on"],
                ends_on=form.cleaned_data["ends_on"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Участие в группе добавлено.")
            return redirect(
                "school_scheduling:membership_edit",
                membership_id=membership.id,
            )
    return render(
        request,
        "scheduling/manager_membership_form.html",
        {"form": form, "title": "Добавить в группу"},
    )


@login_required
def manager_membership_edit(
    request: HttpRequest,
    *,
    membership_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.change_groupmembership",
        "Group membership change permission is required.",
    )
    membership = get_object_or_404(
        GroupMembership.objects.select_related("student", "group"),
        pk=membership_id,
    )
    form = GroupMembershipForm(
        request.POST or None,
        membership=membership,
        initial={
            "starts_on": membership.starts_on,
            "ends_on": membership.ends_on,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_group_membership(
                membership_id=membership.id,
                starts_on=form.cleaned_data["starts_on"],
                ends_on=form.cleaned_data["ends_on"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Период участия в группе обновлён.")
            return redirect(
                "school_scheduling:membership_edit",
                membership_id=membership.id,
            )
    return render(
        request,
        "scheduling/manager_membership_form.html",
        {
            "form": form,
            "membership": membership,
            "title": "Изменить участие в группе",
        },
    )
