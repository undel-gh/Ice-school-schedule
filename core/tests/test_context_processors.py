from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from accounts.models import Student, StudentAccess
from core.context_processors import student_navigation

User = get_user_model()


@pytest.mark.django_db
def test_student_navigation_uses_one_query_and_request_cache(
    django_assert_num_queries,
):
    user = User.objects.create_user(
        username="navigation-query-user",
        password="test",
    )
    active_student = Student.objects.create(display_name="Active student")
    historical_student = Student.objects.create(
        display_name="Historical student",
        is_active=False,
    )
    StudentAccess.objects.create(
        user=user,
        student=active_student,
        role=StudentAccess.Role.SELF,
    )
    StudentAccess.objects.create(
        user=user,
        student=historical_student,
        role=StudentAccess.Role.GUARDIAN,
    )

    request = RequestFactory().get("/")
    request.user = user

    with django_assert_num_queries(1):
        first = student_navigation(request)

    assert first == {
        "student_account_available": True,
        "student_schedule_available": True,
    }

    with django_assert_num_queries(0):
        second = student_navigation(request)

    assert second == first


@pytest.mark.django_db
def test_student_navigation_keeps_historical_account_without_schedule(
    django_assert_num_queries,
):
    user = User.objects.create_user(
        username="historical-navigation-user",
        password="test",
    )
    historical_student = Student.objects.create(
        display_name="Historical only",
        is_active=False,
    )
    StudentAccess.objects.create(
        user=user,
        student=historical_student,
        role=StudentAccess.Role.GUARDIAN,
    )
    request = RequestFactory().get("/")
    request.user = user

    with django_assert_num_queries(1):
        result = student_navigation(request)

    assert result == {
        "student_account_available": True,
        "student_schedule_available": False,
    }
