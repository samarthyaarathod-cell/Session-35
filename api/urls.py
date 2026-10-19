from django.urls import path

from .views import google_login, pay, send_email, send_sms

urlpatterns = [
    path("send-email/", send_email, name="send-email"),
    path("send-sms/", send_sms, name="send-sms"),
    path("pay/", pay, name="pay"),
    path("auth/google/", google_login, name="google-login"),
]
