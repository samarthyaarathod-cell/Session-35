from django.conf import settings
from django.db import models


class GoogleIdentity(models.Model):
    """Link a Django user to Google's stable subject identifier."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="google_identity")
    subject = models.CharField(max_length=255, unique=True)
    email = models.EmailField()

    def __str__(self):
        return f"Google identity for {self.user}"
