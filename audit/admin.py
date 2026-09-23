from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = (
        "occurred_at",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "actor",
        "correlation_id",
    )
    list_filter = ("event_type", "aggregate_type")
    search_fields = ("aggregate_id", "correlation_id")
