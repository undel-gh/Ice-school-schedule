from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError

from attendance.services import reopen_attendance
from core.admin import ReadOnlyAdmin

from .models import (
    GroupMembership,
    Lesson,
    LessonEnrollment,
    LessonResponse,
    LessonRosterEntry,
    LessonType,
    ScheduleTemplate,
    TrainingGroup,
    Venue,
)


admin.site.register(TrainingGroup)
admin.site.register(Venue)
admin.site.register(LessonType)
admin.site.register(ScheduleTemplate)
admin.site.register(GroupMembership)


@admin.register(Lesson)
class LessonAdmin(ReadOnlyAdmin):
    list_display = ("starts_at", "group", "lesson_type", "coach", "status")
    list_filter = ("status", "lesson_type")
    actions = ("reopen_selected_attendance",)

    @admin.action(permissions=["service"], description="Reopen attendance for selected closed lessons")
    def reopen_selected_attendance(self, request, queryset):
        reopened = 0
        for lesson in queryset:
            try:
                reopen_attendance(
                    lesson_id=lesson.id,
                    actor=request.user,
                    reason="Reopened from Django Admin",
                )
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(
                    request,
                    f"{lesson.id}: {exc}",
                    level=messages.ERROR,
                )
            else:
                reopened += 1

        if reopened:
            self.message_user(
                request,
                f"Reopened {reopened} lesson(s).",
                level=messages.SUCCESS,
            )


@admin.register(LessonEnrollment)
class LessonEnrollmentAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "reason", "cancelled_at")


@admin.register(LessonRosterEntry)
class LessonRosterEntryAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "source", "is_active")


@admin.register(LessonResponse)
class LessonResponseAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "status", "updated_at")
