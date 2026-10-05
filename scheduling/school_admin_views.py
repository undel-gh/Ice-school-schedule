from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Student
from core.permissions import require_permission
from core.presentation import validation_message
from core.time import school_date

from .capacity import group_capacity_snapshot
from .models import GroupMembership, LessonType, TrainingGroup, Venue
from .reference_data_services import (
    create_lesson_type,
    create_venue,
    update_lesson_type,
    update_venue,
)
from .school_admin_forms import (
    GroupMembershipForm,
    LessonTypeForm,
    TrainingGroupForm,
    VenueForm,
)
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
    page_obj = Paginator(groups, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "scheduling/manager_groups.html",
        {
            "groups": page_obj.object_list,
            "page_obj": page_obj,
            "query": query,
        },
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
                capacity=form.cleaned_data["capacity"],
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
    today = school_date(timezone.now())
    memberships = group.memberships.select_related("student").order_by(
        "-starts_on", "student__display_name", "id"
    )
    seat_reservations = (
        group.seat_reservations.filter(
            cancelled_at__isnull=True,
            ends_on__gte=today,
        )
        .select_related("student")
        .order_by("starts_on", "student__display_name", "id")
    )
    capacity = group_capacity_snapshot(
        group=group,
        on_date=today,
    )
    return render(
        request,
        "scheduling/manager_group_detail.html",
        {
            "group": group,
            "memberships": memberships,
            "seat_reservations": seat_reservations,
            "capacity": capacity,
            "today": today,
        },
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
            "capacity": group.capacity,
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
                capacity=form.cleaned_data["capacity"],
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
def manager_lesson_types(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_lessontype",
        "Для просмотра типов занятий требуется соответствующее право.",
    )
    query = request.GET.get("q", "").strip()
    lesson_types = LessonType.objects.order_by("-is_active", "name", "id")
    if query:
        lesson_types = lesson_types.filter(
            Q(name__icontains=query) | Q(code__icontains=query)
        )
    page_obj = Paginator(lesson_types, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "scheduling/manager_lesson_types.html",
        {
            "lesson_types": page_obj.object_list,
            "page_obj": page_obj,
            "query": query,
        },
    )


@login_required
def manager_lesson_type_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.add_lessontype",
        "Для создания типа занятия требуется соответствующее право.",
    )
    form = LessonTypeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            lesson_type = create_lesson_type(
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                subscription_category=form.cleaned_data["subscription_category"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Тип занятия создан.")
            return redirect(
                "school_scheduling:lesson_type_edit",
                lesson_type_id=lesson_type.id,
            )
    return render(
        request,
        "scheduling/manager_reference_form.html",
        {
            "form": form,
            "title": "Новый тип занятия",
            "back_url_name": "school_scheduling:lesson_types",
        },
    )


@login_required
def manager_lesson_type_edit(
    request: HttpRequest,
    *,
    lesson_type_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.change_lessontype",
        "Для изменения типа занятия требуется соответствующее право.",
    )
    lesson_type = get_object_or_404(LessonType, pk=lesson_type_id)
    form = LessonTypeForm(
        request.POST or None,
        initial={
            "code": lesson_type.code,
            "name": lesson_type.name,
            "subscription_category": lesson_type.subscription_category,
            "is_active": lesson_type.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_lesson_type(
                lesson_type_id=lesson_type.id,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                subscription_category=form.cleaned_data["subscription_category"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Тип занятия обновлён.")
            return redirect(
                "school_scheduling:lesson_type_edit",
                lesson_type_id=lesson_type.id,
            )
    return render(
        request,
        "scheduling/manager_reference_form.html",
        {
            "form": form,
            "title": "Редактирование типа занятия",
            "back_url_name": "school_scheduling:lesson_types",
            "audit_aggregate_type": "LessonType",
            "audit_aggregate_id": lesson_type.id,
        },
    )


@login_required
def manager_venues(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_venue",
        "Для просмотра площадок требуется соответствующее право.",
    )
    query = request.GET.get("q", "").strip()
    venues = Venue.objects.order_by("-is_active", "name", "id")
    if query:
        venues = venues.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(address__icontains=query)
        )
    page_obj = Paginator(venues, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "scheduling/manager_venues.html",
        {
            "venues": page_obj.object_list,
            "page_obj": page_obj,
            "query": query,
        },
    )


@login_required
def manager_venue_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.add_venue",
        "Для создания площадки требуется соответствующее право.",
    )
    form = VenueForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            venue = create_venue(
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                address=form.cleaned_data["address"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Площадка создана.")
            return redirect(
                "school_scheduling:venue_edit",
                venue_id=venue.id,
            )
    return render(
        request,
        "scheduling/manager_reference_form.html",
        {
            "form": form,
            "title": "Новая площадка",
            "back_url_name": "school_scheduling:venues",
        },
    )


@login_required
def manager_venue_edit(
    request: HttpRequest,
    *,
    venue_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.change_venue",
        "Для изменения площадки требуется соответствующее право.",
    )
    venue = get_object_or_404(Venue, pk=venue_id)
    form = VenueForm(
        request.POST or None,
        initial={
            "code": venue.code,
            "name": venue.name,
            "address": venue.address,
            "is_active": venue.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_venue(
                venue_id=venue.id,
                code=form.cleaned_data["code"],
                name=form.cleaned_data["name"],
                address=form.cleaned_data["address"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Площадка обновлена.")
            return redirect(
                "school_scheduling:venue_edit",
                venue_id=venue.id,
            )
    return render(
        request,
        "scheduling/manager_reference_form.html",
        {
            "form": form,
            "title": "Редактирование площадки",
            "back_url_name": "school_scheduling:venues",
            "audit_aggregate_type": "Venue",
            "audit_aggregate_id": venue.id,
        },
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
    page_obj = Paginator(memberships, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "scheduling/manager_memberships.html",
        {
            "memberships": page_obj.object_list,
            "page_obj": page_obj,
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
