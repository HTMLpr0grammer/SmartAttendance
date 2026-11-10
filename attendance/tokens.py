"""Signed, short-lived JWTs embedded in the rotating classroom QR code."""
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

CONF = settings.SMARTATTENDANCE


class TokenError(Exception):
    """Raised when a QR token is expired, tampered or malformed.

    ``session_id`` is populated when the signature was valid but the token
    expired, so the audit log can still be linked to the right session.
    """

    def __init__(self, message, session_id=None):
        super().__init__(message)
        self.session_id = session_id


def issue_qr_token(session):
    now = timezone.now()
    exp = now + timedelta(seconds=CONF["QR_TOKEN_TTL_SECONDS"])
    payload = {
        "session_id": session.pk,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, CONF["QR_JWT_SECRET"], algorithm=CONF["QR_JWT_ALGORITHM"])
    return token, exp


def decode_qr_token(token):
    if not token or not isinstance(token, str):
        raise TokenError("Missing QR token.")
    key = CONF["QR_JWT_SECRET"]
    alg = CONF["QR_JWT_ALGORITHM"]
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[alg],
            leeway=CONF["QR_TOKEN_LEEWAY_SECONDS"],
            options={"require": ["exp", "iat", "session_id"]},
        )
    except jwt.ExpiredSignatureError:
        # Signature is valid, only the clock ran out – recover the session id for auditing.
        try:
            stale = jwt.decode(token, key, algorithms=[alg], options={"verify_exp": False})
            sid = stale.get("session_id")
        except jwt.PyJWTError:
            sid = None
        raise TokenError("QR code expired. Please scan the current code.", session_id=sid)
    except jwt.PyJWTError:
        raise TokenError("Invalid or tampered QR code.")

    try:
        payload["session_id"] = int(payload["session_id"])
    except (TypeError, ValueError):
        raise TokenError("Invalid or tampered QR code.")
    return payload
