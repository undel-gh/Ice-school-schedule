from django.db import models
from django.utils.translation import gettext_lazy as _


class SubscriptionCategory(models.TextChoices):
    ICE = "ice", _("ICE")
    HALL = "hall", _("HALL")
