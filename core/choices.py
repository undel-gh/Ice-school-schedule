from django.db import models


class SubscriptionCategory(models.TextChoices):
    ICE = "ice", "ICE"
    HALL = "hall", "HALL"
