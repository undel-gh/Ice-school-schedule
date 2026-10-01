from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AccountInvitation,
    CoachProfile,
    ExternalIdentity,
    Student,
    StudentAccess,
), User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    pass


admin.site.register(Student)
admin.site.register(StudentAccess)
admin.site.register(CoachProfile)
admin.site.register(ExternalIdentity)



@admin.register(AccountInvitation)
class AccountInvitationAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "student",
        "coach_display_name",
        "expires_at",
        "accepted_at",
        "revoked_at",
        "created_at",
    )
    list_filter = ("kind",)
    readonly_fields = (
        "kind",
        "token_hash",
        "student",
        "student_access_role",
        "coach_display_name",
        "expires_at",
        "created_at",
        "created_by",
        "accepted_at",
        "accepted_by",
        "revoked_at",
        "revoked_by",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
