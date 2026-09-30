from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render

from core.permissions import require_permission

from .models import AuditEvent


@login_required
def manager_audit_events(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "audit.view_auditevent",
        "Audit event view permission is required.",
    )
    events = AuditEvent.objects.select_related("actor").order_by(
        "-occurred_at", "-id"
    )

    event_type = request.GET.get("event_type", "").strip()
    aggregate_type = request.GET.get("aggregate_type", "").strip()
    aggregate_id = request.GET.get("aggregate_id", "").strip()
    correlation_id = request.GET.get("correlation_id", "").strip()

    if event_type:
        events = events.filter(event_type=event_type)
    if aggregate_type:
        events = events.filter(aggregate_type=aggregate_type)
    if aggregate_id:
        try:
            events = events.filter(aggregate_id=UUID(aggregate_id))
        except ValueError as exc:
            raise Http404("Invalid aggregate id.") from exc
    if correlation_id:
        try:
            events = events.filter(correlation_id=UUID(correlation_id))
        except ValueError as exc:
            raise Http404("Invalid correlation id.") from exc

    return render(
        request,
        "audit/manager_audit_events.html",
        {
            "events": events[:500],
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "correlation_id": correlation_id,
        },
    )
