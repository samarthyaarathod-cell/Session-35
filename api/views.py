import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import google.auth.transport.requests
import requests
import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from google.oauth2 import id_token
from .models import GoogleIdentity

logger = logging.getLogger(__name__)


@api_view(["POST"])
def send_email(request):
    """Send a welcome email through the Mailgun Messages API."""
    email = request.data.get("email", "")
    if not isinstance(email, str) or "@" not in email or len(email) > 254:
        return Response({"error": "Provide a valid email address."}, status=status.HTTP_400_BAD_REQUEST)
    if not all((settings.MAILGUN_API_KEY, settings.MAILGUN_DOMAIN, settings.MAILGUN_FROM_EMAIL)):
        return Response({"error": "Mailgun is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        result = requests.post(
            f"https://api.mailgun.net/v3/{settings.MAILGUN_DOMAIN}/messages",
            auth=("api", settings.MAILGUN_API_KEY),
            data={
                "from": settings.MAILGUN_FROM_EMAIL,
                "to": [email],
                "subject": "Welcome!",
                "text": "Welcome! Thanks for joining FoodieHub.",
            },
            timeout=10,
        )
    except requests.Timeout:
        return Response({"error": "Mailgun request timed out."}, status=status.HTTP_504_GATEWAY_TIMEOUT)
    except requests.RequestException:
        logger.exception("Mailgun request failed")
        return Response({"error": "Mail delivery service is unavailable."}, status=status.HTTP_502_BAD_GATEWAY)
    if result.status_code not in (200, 202):
        logger.warning("Mailgun rejected a message with HTTP %s", result.status_code)
        return Response({"error": "Mailgun did not accept the message."}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({"status": "accepted", "email": email}, status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def send_sms(request):
    """Send an SMS using the configured Twilio account."""
    phone_number = request.data.get("phone_number", "")
    message = request.data.get("message", "")
    if not isinstance(phone_number, str) or not phone_number.startswith("+") or not phone_number[1:].isdigit():
        return Response({"error": "phone_number must be in E.164 format, such as +14155552671."}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(message, str) or not message.strip() or len(message) > 1600:
        return Response({"error": "message must contain 1 to 1600 characters."}, status=status.HTTP_400_BAD_REQUEST)
    if not all((settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_FROM_NUMBER)):
        return Response({"error": "Twilio is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        sms = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).messages.create(
            to=phone_number, from_=settings.TWILIO_FROM_NUMBER, body=message.strip()
        )
    except TwilioRestException as exc:
        logger.warning("Twilio rejected an SMS with code %s", exc.code)
        return Response({"error": "Twilio did not accept the SMS."}, status=status.HTTP_502_BAD_GATEWAY)
    except Exception:
        logger.exception("Twilio request failed")
        return Response({"error": "SMS service is unavailable."}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({"status": sms.status or "queued", "message_id": sms.sid}, status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def pay(request):
    """Create a Stripe test-mode PaymentIntent without claiming the payment is complete."""
    try:
        amount = Decimal(str(request.data.get("amount", "")))
    except (InvalidOperation, ValueError):
        return Response({"error": "amount must be a positive decimal amount."}, status=status.HTTP_400_BAD_REQUEST)
    currency = request.data.get("currency", "")
    if not amount.is_finite() or amount <= 0:
        return Response({"error": "amount must be a positive decimal amount."}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        return Response({"error": "currency must be a 3-letter ISO currency code."}, status=status.HTTP_400_BAD_REQUEST)
    if not settings.STRIPE_SECRET_KEY:
        return Response({"error": "Stripe is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if not settings.STRIPE_SECRET_KEY.startswith("sk_test_"):
        return Response({"error": "This demo endpoint accepts Stripe test-mode keys only."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    exponent = 0 if currency.lower() in {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"} else 3 if currency.lower() in {"bhd", "jod", "kwd", "omr", "tnd"} else 2
    scale = Decimal(10) ** exponent
    minor_units = int((amount * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if minor_units < 1:
        return Response({"error": "amount must be at least 0.01."}, status=status.HTTP_400_BAD_REQUEST)
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        intent = stripe.PaymentIntent.create(
            amount=minor_units,
            currency=currency.lower(),
            payment_method="pm_card_visa",
            confirm=True,
            metadata={"integration": "session-6-demo"},
        )
    except stripe.error.StripeError as exc:
        logger.warning("Stripe rejected PaymentIntent creation: %s", exc.user_message or exc.code)
        return Response({"error": "Stripe could not create the payment intent."}, status=status.HTTP_502_BAD_GATEWAY)
    return Response(
        {"payment_status": intent.status, "transaction_id": intent.id, "amount": str(amount), "currency": currency.lower()},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def google_login(request):
    """Verify a Google ID token, provision the user, and return SimpleJWT tokens."""
    credential = request.data.get("credential") or request.data.get("id_token")
    if not isinstance(credential, str) or not credential:
        return Response({"error": "Provide a Google ID token in the id_token field."}, status=status.HTTP_400_BAD_REQUEST)
    if not settings.GOOGLE_CLIENT_ID:
        return Response({"error": "Google login is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        claims = id_token.verify_oauth2_token(
            credential, google.auth.transport.requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError:
        return Response({"error": "The Google ID token is invalid or expired."}, status=status.HTTP_401_UNAUTHORIZED)
    email = claims.get("email", "").strip().lower()
    if not email or not claims.get("email_verified"):
        return Response({"error": "Google must provide a verified email address."}, status=status.HTTP_401_UNAUTHORIZED)

    subject = claims.get("sub")
    if not subject:
        return Response({"error": "Google ID token did not include a subject identifier."}, status=status.HTTP_401_UNAUTHORIZED)
    try:
        identity = GoogleIdentity.objects.select_related("user").get(subject=subject)
        user = identity.user
        if identity.email != email:
            identity.email = email
            identity.save(update_fields=["email"])
    except GoogleIdentity.DoesNotExist:
        user_model = get_user_model()
        try:
            with transaction.atomic():
                user = user_model.objects.create(
                    username=_unique_username(user_model, subject),
                    email=email,
                )
                user.set_unusable_password()
                user.save(update_fields=["password"])
                GoogleIdentity.objects.create(user=user, subject=subject, email=email)
        except IntegrityError:
            # A concurrent login may have created the same Google subject.
            identity = GoogleIdentity.objects.select_related("user").get(subject=subject)
            user = identity.user
    refresh = RefreshToken.for_user(user)
    return Response({"access": str(refresh.access_token), "refresh": str(refresh)}, status=status.HTTP_200_OK)


def _unique_username(user_model, subject):
    base = "google_" + "".join(char for char in subject if char.isalnum())[:24]
    candidate = base or "google_user"
    suffix = 1
    while user_model.objects.filter(username=candidate).exists():
        candidate = f"{base[:25]}_{suffix}"
        suffix += 1
    return candidate
