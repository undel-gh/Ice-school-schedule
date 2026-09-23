from django.contrib import admin


class ReadOnlyAdmin(admin.ModelAdmin):
    """Allow inspection in Django Admin but no direct mutation."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def save_model(self, request, obj, form, change):
        raise PermissionError("Read-only admin; use an application service.")

    def delete_model(self, request, obj):
        raise PermissionError("Read-only admin; use an application service.")
