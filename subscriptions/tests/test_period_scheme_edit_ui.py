from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from subscriptions.models import SubscriptionPeriodScheme, SubscriptionPlan


User = get_user_model()


def _manager():
    return User.objects.create_user(
        username="period-scheme-guidance-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_period_scheme_edit_locks_immutable_referenced_settings(client):
    manager = _manager()
    scheme = SubscriptionPeriodScheme.objects.create(
        code="guidance-period",
        name="Guidance period",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    SubscriptionPlan.objects.create(
        code="guidance-plan",
        name="Guidance plan",
        period_scheme=scheme,
        is_active=True,
    )
    client.force_login(manager)

    response = client.get(
        reverse(
            "subscriptions:manager_period_scheme_edit",
            kwargs={"scheme_id": scheme.id},
        )
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "После привязки модели к тарифу или расчётному периоду" in body
    assert "создайте новую модель расчётного периода" in body
    assert "Код и название существующей модели" in body
    assert "поле зафиксировано после первого использования модели" in body
    assert 'type="hidden" name="mode"' in body
    assert 'name="fixed_anchor_date"' in body
    assert '<select name="mode"' not in body


@pytest.mark.django_db
def test_period_scheme_edit_keeps_unreferenced_settings_editable(client):
    manager = _manager()
    scheme = SubscriptionPeriodScheme.objects.create(
        code="editable-guidance-period",
        name="Editable guidance period",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    client.force_login(manager)

    response = client.get(
        reverse(
            "subscriptions:manager_period_scheme_edit",
            kwargs={"scheme_id": scheme.id},
        )
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert '<select name="mode"' in body
    assert "поле зафиксировано после первого использования модели" not in body
