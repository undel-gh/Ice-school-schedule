from django.urls import path

from . import student_views

app_name = "student_account"

urlpatterns = [
    path("", student_views.account, name="account"),
]
