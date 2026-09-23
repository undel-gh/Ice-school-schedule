from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import AbsenceJustification, Attendance


@admin.register(Attendance)
class AttendanceAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "status", "marked_at")
    list_filter = ("status",)


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
