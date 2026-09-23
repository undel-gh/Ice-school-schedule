import uuid

from django.conf import settings
from django.db import models

from core.models import UUIDModel


class AuditEvent(UUIDModel):
    event_type = models.CharField(max_length=80)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    aggregate_type = models.CharField(max_length=64)
    aggregate_id = models.UUIDField()
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["aggregate_type", "aggregate_id", "occurred_at"], name="audit_aggregate_time_idx"),
            models.Index(fields=["event_type", "occurred_at"], name="audit_event_time_idx"),
            models.Index(
                fields=["event_type", "aggregate_type", "aggregate_id"],
                name="audit_event_aggregate_idx",
            ),
            models.Index(fields=["correlation_id"], name="audit_correlation_idx"),
        ]
