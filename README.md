# Session 6: Advanced Integrations and Deployment

Standalone Django REST Framework project for Mailgun email, Twilio SMS, Stripe test payments, and Google sign-in with JWTs.

## Local setup

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in the `.env` values, then run:

```powershell
python manage.py migrate
python manage.py runserver
```

All provider secrets stay server-side in environment variables. Endpoints other than Google login require a valid `Authorization: Bearer <access-token>` JWT. Obtain one by posting a Google ID token in `credential` (or `id_token`) to `/api/auth/google/`; the Google client ID in `.env` must match the token audience.

## Endpoints

- `POST /api/send-email/` with `{ "email": "recipient@example.com" }` calls Mailgun and returns `202` only when Mailgun accepts the welcome message.
- `POST /api/send-sms/` with `{ "phone_number": "+14155552671", "message": "Hello" }` sends through Twilio and returns a message ID.
- `POST /api/pay/` with `{ "amount": "10.00", "currency": "usd" }` creates and confirms a Stripe test PaymentIntent using Stripe's `pm_card_visa` test payment method. The response contains `payment_status` and `transaction_id`. This demo deliberately refuses non-test Stripe secret keys. Amount is expressed in major currency units and converted to the currency's minor units.
- `POST /api/auth/google/` with `{ "credential": "<Google ID token>" }` validates the token's signature, audience, and expiry; links the account by Google's stable subject; and returns SimpleJWT `access` and `refresh` tokens.

Mailgun must have a sending domain verified and the recipient must be permitted by the account's current sending policy. Twilio trial accounts can message only verified destination numbers. Stripe test keys cannot move real funds. Google login requires a configured OAuth Web client and a genuine ID token from it.

## Postman

Import `postman/Session-6-Integrations.postman_collection.json`. Run **Google Login** with a genuine ID token; its test stores the returned access token for the protected integration requests. Then send the email, SMS, and Stripe requests with provider credentials configured in the server environment. The email test checks for HTTP 202 accepted; the payment test checks for a succeeded test intent and transaction ID.

## Deploy to PythonAnywhere

1. Create a PythonAnywhere account and web app using a manual Django configuration.
2. Clone or upload this `session-6` folder to your account and create a virtual environment using the same Python version as the web app.
3. Install `requirements.txt` in that environment, set the Web tab's virtualenv path and environment variables, then run `python manage.py migrate` in a Bash console.
4. Adapt `deployment/pythonanywhere_wsgi_example.txt` in the Web tab's WSGI configuration. Set `DJANGO_ALLOWED_HOSTS` to your actual `username.pythonanywhere.com` hostname and `DJANGO_DEBUG=false`; provide all provider credentials as environment variables.
5. Reload the web app. In Postman, set `base_url` to `https://YOUR_USERNAME.pythonanywhere.com`, log in with Google, then run the welcome email request and save a screenshot of the live response for submission.

This workspace cannot create the external PythonAnywhere account, configure its credentials, or produce a genuine live response screenshot. Do not present a local response as a deployed result.
