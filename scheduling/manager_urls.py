from django.urls import path

from . import manager_views

app_name = "scheduling_manager"

urlpatterns = [
    path("templates/", manager_views.manager_schedule_templates, name="templates"),
    path("templates/new/", manager_views.manager_schedule_template_create, name="template_create"),
    path("templates/<uuid:template_id>/", manager_views.manager_schedule_template_detail, name="template_detail"),
    path("templates/<uuid:template_id>/version/", manager_views.manager_schedule_template_version, name="template_version"),
    path("conflicts/", manager_views.manager_generation_conflicts, name="conflicts"),
    path("conflicts/<uuid:event_id>/skip/", manager_views.manager_skip_generation_conflict, name="conflict_skip"),
    path("lessons/", manager_views.manager_lessons, name="lessons"),
    path("lessons/<uuid:lesson_id>/", manager_views.manager_lesson_detail, name="lesson_detail"),
    path("lessons/<uuid:lesson_id>/publish/", manager_views.manager_publish_lesson, name="lesson_publish"),
    path("lessons/<uuid:lesson_id>/confirm/", manager_views.manager_confirm_lesson, name="lesson_confirm"),
    path("lessons/<uuid:lesson_id>/cancel/", manager_views.manager_cancel_lesson, name="lesson_cancel"),
    path("lessons/<uuid:lesson_id>/reschedule/", manager_views.manager_reschedule_lesson, name="lesson_reschedule"),
]
