from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from subscriptions.models import SubscriptionPeriodScheme, SubscriptionPlan


User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="period-scheme-edit-manager",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


@pytest.mark.django_db
def test_manager_can_change_mode_and_anchor_before_scheme_is_referenced(
    client,
    manager,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="editable-period",
        name="Редактируемый период",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "subscriptions:manager_period_scheme_edit",
            kwargs={"scheme_id": scheme.id},
        ),
        {
            "code": scheme.code,
            "name": "Фиксированный период",
            "mode": SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
            "fixed_anchor_date": "2027-01-04",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    scheme.refresh_from_db()
    assert scheme.name == "Фиксированный период"
    assert scheme.mode == SubscriptionPeriodScheme.Mode.FIXED_28_DAYS
    assert scheme.fixed_anchor_date == date(2027, 1, 4)


@pytest.mark.django_db
def test_django_admin_can_change_mode_and_anchor_before_scheme_is_referenced(
    client,
    manager,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="editable-admin-period",
        name="Редактируемый период Admin",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "admin:subscriptions_subscriptionperiodscheme_change",
            args=[scheme.id],
        ),
        {
            "code": scheme.code,
            "name": "Фиксированный период Admin",
            "mode": SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
            "fixed_anchor_date": "2027-02-01",
            "is_active": "on",
            "_save": "Сохранить",
        },
    )

    assert response.status_code == 302
    scheme.refresh_from_db()
    assert scheme.name == "Фиксированный период Admin"
    assert scheme.mode == SubscriptionPeriodScheme.Mode.FIXED_28_DAYS
    assert scheme.fixed_anchor_date == date(2027, 2, 1)


@pytest.mark.django_db
def test_manager_referenced_scheme_mode_change_is_rejected_visibly(
    client,
    manager,
):
    scheme = SubscriptionPeriodScheme.objects.create(
        code="referenced-period",
        name="Используемый период",
        mode=SubscriptionPeriodScheme.Mode.CALENDAR_MONTH,
        is_active=True,
    )
    SubscriptionPlan.objects.create(
        code="referenced-plan",
        name="Использующий тариф",
        period_scheme=scheme,
        is_active=True,
    )
    client.force_login(manager)

    response = client.post(
        reverse(
            "subscriptions:manager_period_scheme_edit",
            kwargs={"scheme_id": scheme.id},
        ),
        {
            "code": scheme.code,
            "name": scheme.name,
            "mode": SubscriptionPeriodScheme.Mode.FIXED_28_DAYS,
            "fixed_anchor_date": "2027-01-04",
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    scheme.refresh_from_db()
    assert scheme.mode == SubscriptionPeriodScheme.Mode.CALENDAR_MONTH
    assert scheme.fixed_anchor_date is None
    body = response.content.decode().lower()
    assert "используемую модель расчётного периода нельзя менять" in body
    assert "создайте новую модель" in body
