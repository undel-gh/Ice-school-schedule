from django.contrib import admin

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


@admin.register(LessonEnrollment)
class LessonEnrollmentAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "reason", "cancelled_at")


@admin.register(LessonRosterEntry)
class LessonRosterEntryAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "source", "is_active")


@admin.register(LessonResponse)
class LessonResponseAdmin(ReadOnlyAdmin):
    list_display = ("lesson", "student", "status", "updated_at")
