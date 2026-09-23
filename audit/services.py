from __future__ import annotations

from uuid import UUID

from django.contrib.auth import get_user_model

from .models import AuditEvent

User = get_user_model()


def record_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    actor: User | None,
    payload: dict | None = None,
    correlation_id: UUID | None = None,
) -> AuditEvent:
    values = {
        "event_type": event_type,
        "actor": actor,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "payload": payload or {},
    }
    if correlation_id is not None:
        values["correlation_id"] = correlation_id
    return AuditEvent.objects.create(**values)


def event_exists(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
) -> bool:
    return AuditEvent.objects.filter(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
    ).exists()



def event_exists_with_payload(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload_filters: dict[str, object],
) -> bool:
    queryset = AuditEvent.objects.filter(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
    )
    for key, value in payload_filters.items():
        queryset = queryset.filter(**{f"payload__{key}": value})
    return queryset.exists()
