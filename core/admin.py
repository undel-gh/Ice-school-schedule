from django.contrib import admin


class ReadOnlyAdmin(admin.ModelAdmin):
    """Inspect operational state without allowing direct mutation."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_service_permission(self, request):
        opts = self.model._meta
        return request.user.has_perm(
            f"{opts.app_label}.change_{opts.model_name}"
        )
