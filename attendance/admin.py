from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.admin import ReadOnlyAdmin

from .models import AbsenceJustification, Attendance
from .services import set_attendance


@admin.register(Attendance)
class AttendanceAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "status", "marked_at")
    list_filter = ("status",)
    actions = ("mark_present", "mark_absent")

    @admin.action(description="Mark selected attendance as present")
    def mark_present(self, request, queryset):
        self._set_status(
            request=request,
            queryset=queryset,
            status=Attendance.Status.PRESENT,
        )

    @admin.action(description="Mark selected attendance as absent")
    def mark_absent(self, request, queryset):
        self._set_status(
            request=request,
            queryset=queryset,
            status=Attendance.Status.ABSENT,
        )

    def _set_status(self, *, request, queryset, status):
        changed = 0
        for attendance in queryset.select_related("lesson"):
            try:
                set_attendance(
                    lesson_id=attendance.lesson_id,
                    student_id=attendance.student_id,
                    status=status,
                    actor=request.user,
                    now=timezone.now(),
                )
            except ValidationError as exc:
                self.message_user(
                    request,
                    f"{attendance}: {exc}",
                    level=messages.ERROR,
                )
            else:
                changed += 1

        if changed:
            self.message_user(
                request,
                f"Updated {changed} attendance record(s).",
                level=messages.SUCCESS,
            )


@admin.register(AbsenceJustification)
class AbsenceJustificationAdmin(ReadOnlyAdmin):
    list_display = (
        "lesson",
        "student",
        "type",
        "status",
        "declared_at",
        "reviewed_at",
    )
    list_filter = ("type", "status")
