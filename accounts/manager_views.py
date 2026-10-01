from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.permissions import require_permission
from core.presentation import validation_message

from .forms import (
    AccountInvitationForm,
    CoachProfileForm,
    StudentAccessForm,
    StudentForm,
)
from .models import AccountInvitation, CoachProfile, Student, StudentAccess
from .onboarding import (
    create_account_invitation,
    revoke_account_invitation,
)
from .services import (
    create_coach_profile,
    create_student,
    create_student_access,
    update_coach_profile,
    update_student,
    update_student_access,
)


@login_required
def manager_students(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.view_student",
        "Student view permission is required.",
    )
    query = request.GET.get("q", "").strip()
    students = Student.objects.order_by("-is_active", "display_name", "id")
    if query:
        students = students.filter(display_name__icontains=query)
    page_obj = Paginator(students, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "accounts/manager_students.html",
        {
            "students": page_obj.object_list,
            "page_obj": page_obj,
            "query": query,
        },
    )


@login_required
def manager_student_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.add_student",
        "Student creation permission is required.",
    )
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            student = create_student(
                display_name=form.cleaned_data["display_name"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Ученик создан.")
            return redirect("accounts_manager:student_detail", student_id=student.id)
    return render(
        request,
        "accounts/manager_student_form.html",
        {"form": form, "title": "Новый ученик"},
    )


@login_required
def manager_student_detail(
    request: HttpRequest,
    *,
    student_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.view_student",
        "Student view permission is required.",
    )
    student = get_object_or_404(Student, pk=student_id)
    accesses = student.accesses.select_related("user").order_by("-is_active", "role", "id")
    memberships = student.group_memberships.select_related("group").order_by("-starts_on", "id")
    return render(
        request,
        "accounts/manager_student_detail.html",
        {
            "student": student,
            "accesses": accesses,
            "memberships": memberships,
        },
    )


@login_required
def manager_student_edit(
    request: HttpRequest,
    *,
    student_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.change_student",
        "Student change permission is required.",
    )
    student = get_object_or_404(Student, pk=student_id)
    form = StudentForm(
        request.POST or None,
        initial={
            "display_name": student.display_name,
            "is_active": student.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_student(
                student_id=student.id,
                display_name=form.cleaned_data["display_name"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Данные ученика обновлены.")
            return redirect("accounts_manager:student_detail", student_id=student.id)
    return render(
        request,
        "accounts/manager_student_form.html",
        {"form": form, "title": "Редактирование ученика", "student": student},
    )


@login_required
def manager_student_access_create(
    request: HttpRequest,
    *,
    student_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.add_studentaccess",
        "Student access creation permission is required.",
    )
    student = get_object_or_404(Student, pk=student_id)
    form = StudentAccessForm(request.POST or None, student=student)
    if request.method == "POST" and form.is_valid():
        try:
            create_student_access(
                user_id=form.cleaned_data["user"].id,
                student_id=student.id,
                role=form.cleaned_data["role"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Доступ к ученику добавлен.")
            return redirect("accounts_manager:student_detail", student_id=student.id)
    return render(
        request,
        "accounts/manager_student_access_form.html",
        {"form": form, "student": student, "title": "Новый доступ"},
    )


@login_required
def manager_student_access_edit(
    request: HttpRequest,
    *,
    access_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.change_studentaccess",
        "Student access change permission is required.",
    )
    access = get_object_or_404(
        StudentAccess.objects.select_related("student", "user"),
        pk=access_id,
    )
    form = StudentAccessForm(
        request.POST or None,
        student=access.student,
        access=access,
        initial={"role": access.role, "is_active": access.is_active},
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_student_access(
                access_id=access.id,
                role=form.cleaned_data["role"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Доступ обновлён.")
            return redirect(
                "accounts_manager:student_detail",
                student_id=access.student_id,
            )
    return render(
        request,
        "accounts/manager_student_access_form.html",
        {
            "form": form,
            "student": access.student,
            "access": access,
            "title": "Редактирование доступа",
        },
    )


@login_required
def manager_coaches(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.view_coachprofile",
        "Coach profile view permission is required.",
    )
    query = request.GET.get("q", "").strip()
    coaches = CoachProfile.objects.select_related("user").order_by(
        "-is_active", "display_name", "id"
    )
    if query:
        coaches = coaches.filter(display_name__icontains=query)
    page_obj = Paginator(coaches, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "accounts/manager_coaches.html",
        {
            "coaches": page_obj.object_list,
            "page_obj": page_obj,
            "query": query,
        },
    )


@login_required
def manager_coach_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.add_coachprofile",
        "Coach profile creation permission is required.",
    )
    form = CoachProfileForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            coach = create_coach_profile(
                user_id=form.cleaned_data["user"].id,
                display_name=form.cleaned_data["display_name"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Профиль тренера создан.")
            return redirect("accounts_manager:coach_edit", coach_id=coach.id)
    return render(
        request,
        "accounts/manager_coach_form.html",
        {"form": form, "title": "Новый тренер"},
    )


@login_required
def manager_coach_edit(
    request: HttpRequest,
    *,
    coach_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.change_coachprofile",
        "Coach profile change permission is required.",
    )
    coach = get_object_or_404(CoachProfile.objects.select_related("user"), pk=coach_id)
    form = CoachProfileForm(
        request.POST or None,
        coach=coach,
        initial={
            "display_name": coach.display_name,
            "is_active": coach.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_coach_profile(
                coach_id=coach.id,
                display_name=form.cleaned_data["display_name"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Профиль тренера обновлён.")
            return redirect("accounts_manager:coaches")
    return render(
        request,
        "accounts/manager_coach_form.html",
        {"form": form, "coach": coach, "title": "Редактирование тренера"},
    )



@login_required
def manager_account_invitations(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.view_accountinvitation",
        "Account invitation view permission is required.",
    )
    invitations = (
        AccountInvitation.objects.select_related(
            "student",
            "created_by",
            "accepted_by",
            "revoked_by",
        )
        .order_by("-created_at", "id")[:300]
    )
    return render(
        request,
        "accounts/manager_invitations.html",
        {
            "invitations": invitations,
            "now": timezone.now(),
        },
    )


@login_required
@never_cache
def manager_account_invitation_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.add_accountinvitation",
        "Account invitation creation permission is required.",
    )
    form = AccountInvitationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.cleaned_data.get("student")
        expires_at = timezone.now() + timedelta(
            hours=form.cleaned_data["expires_in_hours"]
        )
        try:
            created = create_account_invitation(
                kind=form.cleaned_data["kind"],
                actor=request.user,
                student_id=student.id if student is not None else None,
                student_access_role=form.cleaned_data.get(
                    "student_access_role",
                    "",
                ),
                coach_display_name=form.cleaned_data.get(
                    "coach_display_name",
                    "",
                ),
                expires_at=expires_at,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            invitation_url = request.build_absolute_uri(
                reverse(
                    "external_auth:invitation",
                    kwargs={"token": created.token},
                )
            )
            return render(
                request,
                "accounts/manager_invitation_created.html",
                {
                    "invitation": created.invitation,
                    "invitation_url": invitation_url,
                },
            )
    return render(
        request,
        "accounts/manager_invitation_form.html",
        {"form": form},
    )


@login_required
@require_POST
def manager_account_invitation_revoke(
    request: HttpRequest,
    *,
    invitation_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "accounts.change_accountinvitation",
        "Account invitation change permission is required.",
    )
    invitation = get_object_or_404(AccountInvitation, pk=invitation_id)
    try:
        revoke_account_invitation(
            invitation_id=invitation.id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Приглашение отозвано.")
    return redirect("accounts_manager:invitations")
