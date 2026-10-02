import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from audit.models import AuditEvent

User = get_user_model()


@pytest.mark.django_db
def test_manager_audit_view_filters_by_aggregate(client):
    manager = User.objects.create_user(
        username="audit-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    first = AuditEvent.objects.create(
        event_type="ExampleEvent",
        actor=manager,
        aggregate_type="Example",
        aggregate_id=manager.id,
        payload={"visible": True},
    )
    AuditEvent.objects.create(
        event_type="OtherEvent",
        actor=manager,
        aggregate_type="Other",
        aggregate_id=manager.id,
        payload={"visible": False},
    )
    client.force_login(manager)

    response = client.get(
        reverse("audit_manager:events"),
        {
            "aggregate_type": "Example",
            "aggregate_id": str(manager.id),
        },
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert first.event_type in body
    assert "OtherEvent" not in body
    assert "visible" in body


@pytest.mark.django_db
def test_manager_audit_view_localizes_known_event_and_aggregate(client):
    manager = User.objects.create_user(
        username="audit-localized-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )
    AuditEvent.objects.create(
        event_type="LessonCancelled",
        actor=None,
        aggregate_type="Lesson",
        aggregate_id=manager.id,
        payload={},
    )
    client.force_login(manager)

    response = client.get(reverse("audit_manager:events"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Занятие отменено" in body
    assert "система" in body
    assert ">Занятие<" in body
    assert "LessonCancelled" not in body
    assert ">Lesson<" not in body
    assert "correlation:" not in body
