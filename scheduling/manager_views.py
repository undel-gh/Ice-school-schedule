from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import AuditEvent
from core.permissions import require_permission
from core.presentation import validation_message
from core.time import make_school_aware, school_date
from ice_school.workflows import reschedule_lesson_with_entitlements

from .forms import (
    ManagerLessonCancelForm,
    ManagerLessonRescheduleForm,
    ManagerScheduleTemplateForm,
    ManagerScheduleTemplateVersionForm,
)
from .models import Lesson, ScheduleTemplate
from .services import (
    cancel_lesson,
    confirm_lesson,
    create_schedule_template,
    publish_lesson,
    skip_template_occurrence,
    version_schedule_template,
)

MAX_MANAGER_RANGE_DAYS = 366


def _parse_date(value: str | None, *, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Http404("Invalid date.") from exc


@login_required
def manager_schedule_templates(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_scheduletemplate",
        "Schedule template view permission is required.",
    )
    templates = ScheduleTemplate.objects.select_related(
        "group", "lesson_type", "coach", "venue"
    ).order_by("-is_active", "group__name", "weekday", "start_time", "-valid_from")
    return render(
        request,
        "scheduling/manager_schedule_templates.html",
        {"templates": templates},
    )


@login_required
def manager_schedule_template_create(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.add_scheduletemplate",
        "Schedule template creation permission is required.",
    )
    form = ManagerScheduleTemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            template = create_schedule_template(
                group_id=form.cleaned_data["group"].id,
                lesson_type_id=form.cleaned_data["lesson_type"].id,
                coach_id=form.cleaned_data["coach"].id,
                venue_id=form.cleaned_data["venue"].id,
                weekday=form.cleaned_data["weekday"],
                start_time=form.cleaned_data["start_time"],
                duration_minutes=form.cleaned_data["duration_minutes"],
                valid_from=form.cleaned_data["valid_from"],
                valid_until=form.cleaned_data["valid_until"],
                minimum_attendees_override=form.cleaned_data["minimum_attendees_override"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Шаблон расписания создан.")
            return redirect(
                "scheduling_manager:template_detail",
                template_id=template.id,
            )
    return render(
        request,
        "scheduling/manager_schedule_template_form.html",
        {"form": form, "title": "Новый шаблон расписания"},
    )


@login_required
def manager_schedule_template_detail(
    request: HttpRequest,
    *,
    template_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_scheduletemplate",
        "Schedule template view permission is required.",
    )
    template = get_object_or_404(
        ScheduleTemplate.objects.select_related(
            "group", "lesson_type", "coach", "venue"
        ),
        pk=template_id,
    )
    lessons = template.generated_lessons.select_related(
        "lesson_type", "group", "coach", "venue"
    ).order_by("-starts_at")[:30]
    return render(
        request,
        "scheduling/manager_schedule_template_detail.html",
        {"template": template, "lessons": lessons},
    )


@login_required
def manager_schedule_template_version(
    request: HttpRequest,
    *,
    template_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.change_scheduletemplate",
        "Schedule template change permission is required.",
    )
    template = get_object_or_404(ScheduleTemplate, pk=template_id)
    initial = {
        "group": template.group_id,
        "lesson_type": template.lesson_type_id,
        "coach": template.coach_id,
        "venue": template.venue_id,
        "weekday": template.weekday,
        "start_time": template.start_time,
        "duration_minutes": template.duration_minutes,
        "minimum_attendees_override": template.minimum_attendees_override,
        "effective_from": max(
            school_date(timezone.now()),
            template.valid_from + timedelta(days=1),
        ),
    }
    form = ManagerScheduleTemplateVersionForm(
        request.POST or None,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        try:
            new_template = version_schedule_template(
                template_id=template.id,
                effective_from=form.cleaned_data["effective_from"],
                actor=request.user,
                now=timezone.now(),
                group_id=form.cleaned_data["group"].id,
                lesson_type_id=form.cleaned_data["lesson_type"].id,
                coach_id=form.cleaned_data["coach"].id,
                venue_id=form.cleaned_data["venue"].id,
                weekday=form.cleaned_data["weekday"],
                start_time=form.cleaned_data["start_time"],
                duration_minutes=form.cleaned_data["duration_minutes"],
                minimum_attendees_override=form.cleaned_data[
                    "minimum_attendees_override"
                ],
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
        else:
            messages.success(request, "Создана новая версия шаблона.")
            return redirect(
                "scheduling_manager:template_detail",
                template_id=new_template.id,
            )
    return render(
        request,
        "scheduling/manager_schedule_template_form.html",
        {
            "form": form,
            "title": "Новая версия шаблона",
            "template": template,
        },
    )


@login_required
def manager_generation_conflicts(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_scheduletemplate",
        "Schedule conflict view permission is required.",
    )
    events = list(
        AuditEvent.objects.filter(
            event_type="LessonGenerationConflict",
            aggregate_type="ScheduleTemplate",
        ).order_by("-occurred_at")[:200]
    )
    template_ids = {event.aggregate_id for event in events}
    templates = {
        item.id: item
        for item in ScheduleTemplate.objects.filter(id__in=template_ids)
        .select_related("group", "lesson_type")
    }
    parsed_rows = []
    lesson_ids = set()
    expected_starts = set()
    for event in events:
        conflicting_uuid = None
        raw_conflicting_id = event.payload.get("conflicting_lesson_id")
        if raw_conflicting_id:
            try:
                conflicting_uuid = UUID(raw_conflicting_id)
            except (TypeError, ValueError):
                conflicting_uuid = None
        if conflicting_uuid is not None:
            lesson_ids.add(conflicting_uuid)
        try:
            expected_starts_at = datetime.fromisoformat(
                event.payload["expected_starts_at"]
            )
        except (KeyError, TypeError, ValueError):
            expected_starts_at = None
        if expected_starts_at is not None:
            expected_starts.add(expected_starts_at)
        parsed_rows.append(
            (event, conflicting_uuid, expected_starts_at)
        )

    lessons = {
        item.id: item
        for item in Lesson.objects.filter(id__in=lesson_ids)
        .select_related("group", "lesson_type", "coach", "venue")
    }
    materialized_occurrences = {
        (template_id, starts_at): status
        for template_id, starts_at, status in Lesson.objects.filter(
            source_template_id__in=template_ids,
            starts_at__in=expected_starts,
        ).values_list("source_template_id", "starts_at", "status")
    }

    rows = []
    for event, conflicting_uuid, expected_starts_at in parsed_rows:
        occurrence_status = (
            materialized_occurrences.get(
                (event.aggregate_id, expected_starts_at)
            )
            if expected_starts_at is not None
            else None
        )
        rows.append(
            {
                "event": event,
                "template": templates.get(event.aggregate_id),
                "conflicting_lesson": lessons.get(conflicting_uuid),
                "expected_starts_at": expected_starts_at,
                "resolved": occurrence_status is not None,
                "occurrence_status": occurrence_status,
            }
        )
    return render(
        request,
        "scheduling/manager_generation_conflicts.html",
        {"rows": rows},
    )


@login_required
@require_POST
def manager_skip_generation_conflict(
    request: HttpRequest,
    *,
    event_id: UUID,
) -> HttpResponse:
    event = get_object_or_404(
        AuditEvent,
        pk=event_id,
        event_type="LessonGenerationConflict",
        aggregate_type="ScheduleTemplate",
    )
    try:
        occurrence_date = datetime.fromisoformat(
            event.payload["expected_starts_at"]
        ).date()
        skip_template_occurrence(
            template_id=event.aggregate_id,
            occurrence_date=occurrence_date,
            actor=request.user,
            now=timezone.now(),
        )
    except (KeyError, TypeError, ValueError):
        messages.error(request, "Audit-событие конфликта повреждено.")
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Регулярное занятие явно пропущено.")
    return redirect("scheduling_manager:conflicts")


@login_required
def manager_lessons(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_lesson",
        "Lesson view permission is required.",
    )
    today = school_date(timezone.now())
    from_date = _parse_date(request.GET.get("from"), default=today)
    until_date = _parse_date(
        request.GET.get("until"),
        default=from_date + timedelta(days=30),
    )
    if until_date < from_date or (until_date - from_date).days > MAX_MANAGER_RANGE_DAYS:
        raise Http404("Invalid date range.")
    start = make_school_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end = make_school_aware(
        datetime.combine(
            until_date + timedelta(days=1),
            datetime.min.time(),
        )
    )
    lessons = (
        Lesson.objects.filter(starts_at__gte=start, starts_at__lt=end)
        .select_related("group", "lesson_type", "coach", "venue", "source_template")
        .order_by("starts_at", "group__name", "id")
    )
    status = request.GET.get("status")
    if status in Lesson.Status.values:
        lessons = lessons.filter(status=status)
    return render(
        request,
        "scheduling/manager_lessons.html",
        {
            "lessons": lessons,
            "from_date": from_date,
            "until_date": until_date,
            "selected_status": status or "",
            "statuses": Lesson.Status.choices,
        },
    )


@login_required
def manager_lesson_detail(
    request: HttpRequest,
    *,
    lesson_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "scheduling.view_lesson",
        "Lesson view permission is required.",
    )
    lesson = get_object_or_404(
        Lesson.objects.select_related(
            "group", "lesson_type", "coach", "venue", "source_template",
            "replacement_lesson",
        ),
        pk=lesson_id,
    )
    return render(
        request,
        "scheduling/manager_lesson_detail.html",
        {
            "lesson": lesson,
            "cancel_form": ManagerLessonCancelForm(),
            "reschedule_form": ManagerLessonRescheduleForm(
                initial={
                    "new_starts_at": lesson.starts_at,
                    "new_ends_at": lesson.ends_at,
                }
            ),
        },
    )


@login_required
@require_POST
def manager_publish_lesson(request: HttpRequest, *, lesson_id: UUID) -> HttpResponse:
    get_object_or_404(Lesson, pk=lesson_id)
    require_permission(
        request.user,
        "scheduling.change_lesson",
        "Lesson change permission is required.",
    )
    try:
        publish_lesson(
            lesson_id=lesson_id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Занятие опубликовано.")
    return redirect("scheduling_manager:lesson_detail", lesson_id=lesson_id)


@login_required
@require_POST
def manager_confirm_lesson(request: HttpRequest, *, lesson_id: UUID) -> HttpResponse:
    get_object_or_404(Lesson, pk=lesson_id)
    try:
        confirm_lesson(
            lesson_id=lesson_id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Занятие подтверждено.")
    return redirect("scheduling_manager:lesson_detail", lesson_id=lesson_id)


@login_required
@require_POST
def manager_cancel_lesson(request: HttpRequest, *, lesson_id: UUID) -> HttpResponse:
    get_object_or_404(Lesson, pk=lesson_id)
    form = ManagerLessonCancelForm(request.POST)
    if form.is_valid():
        try:
            cancel_lesson(
                lesson_id=lesson_id,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                now=timezone.now(),
            )
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            messages.success(request, "Занятие отменено.")
    else:
        messages.error(request, "Проверьте причину отмены.")
    return redirect("scheduling_manager:lesson_detail", lesson_id=lesson_id)


@login_required
@require_POST
def manager_reschedule_lesson(request: HttpRequest, *, lesson_id: UUID) -> HttpResponse:
    get_object_or_404(Lesson, pk=lesson_id)
    form = ManagerLessonRescheduleForm(request.POST)
    if form.is_valid():
        try:
            replacement = reschedule_lesson_with_entitlements(
                lesson_id=lesson_id,
                new_starts_at=form.cleaned_data["new_starts_at"],
                new_ends_at=form.cleaned_data["new_ends_at"],
                actor=request.user,
                reason=form.cleaned_data["reason"],
                now=timezone.now(),
            )
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            messages.success(request, "Занятие перенесено.")
            return redirect(
                "scheduling_manager:lesson_detail",
                lesson_id=replacement.id,
            )
    else:
        messages.error(request, "Проверьте новые дату и время.")
    return redirect("scheduling_manager:lesson_detail", lesson_id=lesson_id)
