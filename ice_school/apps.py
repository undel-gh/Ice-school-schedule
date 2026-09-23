from django.apps import AppConfig


class IceSchoolConfig(AppConfig):
    name = "ice_school"
    verbose_name = "Ice School"

    def ready(self):
        from . import checks  # noqa: F401
