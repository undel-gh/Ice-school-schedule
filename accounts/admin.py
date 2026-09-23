from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CoachProfile, ExternalIdentity, Student, StudentAccess, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    pass


admin.site.register(Student)
admin.site.register(StudentAccess)
admin.site.register(CoachProfile)
admin.site.register(ExternalIdentity)
