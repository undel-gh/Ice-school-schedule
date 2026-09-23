from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("", views.home, name="home"),
    path("schedule/", views.student_schedule, name="student_schedule"),
    path(
        "schedule/<uuid:student_id>/<uuid:lesson_id>/rsvp/",
        views.set_rsvp,
        name="set_rsvp",
    ),
    path("coach/", views.coach_schedule, name="coach_schedule"),
    path(
        "coach/lesson/<uuid:lesson_id>/",
        views.coach_lesson,
        name="coach_lesson",
    ),
    path(
        "coach/lesson/<uuid:lesson_id>/<uuid:student_id>/attendance/",
        views.coach_set_attendance,
        name="coach_set_attendance",
    ),
    path(
        "coach/lesson/<uuid:lesson_id>/mark-expected-present/",
        views.coach_mark_expected_present,
        name="coach_mark_expected_present",
    ),
    path(
        "coach/lesson/<uuid:lesson_id>/mark-remaining-absent/",
        views.coach_mark_remaining_absent,
        name="coach_mark_remaining_absent",
    ),
    path(
        "coach/lesson/<uuid:lesson_id>/complete/",
        views.coach_complete_lesson,
        name="coach_complete_lesson",
    ),
    path(
        "coach/lesson/<uuid:lesson_id>/submit/",
        views.coach_submit_attendance,
        name="coach_submit_attendance",
    ),
]
