import os
import sys
import hashlib
import secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import jwt
from dotenv import load_dotenv
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

ph = PasswordHasher()

_MIN_JWT_SECRET_BYTES = 32
_DISALLOWED_JWT_SECRETS = {
    "smartpick-insecure-dev-secret-key-change-in-prod-32bytes",
    "change_this_to_a_secure_random_secret_in_production",
    "your_32_byte_jwt_secret_key_here",
    "changeme",
    "secret",
}
_TEST_ONLY_JWT_SECRET = "smartpick-isolated-test-only-secret-key-32bytes!"


def _is_test_environment() -> bool:
    return (
        os.getenv("TESTING", "").lower() in ("true", "1")
        or "PYTEST_CURRENT_TEST" in os.environ
        or "pytest" in sys.modules
    )


def validate_jwt_secret(secret: Optional[str] = None, *, allow_test_fallback: bool = True) -> str:
    """
    Validate and return JWT_SECRET_KEY.

    Fails fast with RuntimeError if missing, a known placeholder, or shorter than
    32 bytes in normal execution. Never includes the secret value in errors.
    """
    candidate = (os.getenv("JWT_SECRET_KEY", "") if secret is None else secret).strip()

    if not candidate:
        if allow_test_fallback and _is_test_environment():
            return _TEST_ONLY_JWT_SECRET
        raise RuntimeError(
            "JWT_SECRET_KEY is not configured. Set a random secret of at least 32 bytes "
            "in the root .env file before starting SmartPick."
        )

    if candidate in _DISALLOWED_JWT_SECRETS:
        if allow_test_fallback and _is_test_environment():
            return _TEST_ONLY_JWT_SECRET
        raise RuntimeError(
            "JWT_SECRET_KEY is set to an insecure placeholder value. "
            "Replace it with a cryptographically random secret of at least 32 bytes."
        )

    if len(candidate.encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
        raise RuntimeError(
            f"JWT_SECRET_KEY is too short (minimum {_MIN_JWT_SECRET_BYTES} bytes required)."
        )

    return candidate


JWT_SECRET_KEY = validate_jwt_secret()
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "5"))
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "60"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))


def hash_password(password: str) -> str:
    """Hash password using Argon2id."""
    return ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against stored Argon2id hash."""
    try:
        return ph.verify(hashed_password, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password meets minimum security standards."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(char.isdigit() for char in password):
        return False, "Password must contain at least one digit."
    if not any(char.isalpha() for char in password):
        return False, "Password must contain at least one letter."
    return True, ""


def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically secure random numeric OTP."""
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def hash_otp(otp: str, salt: str = "") -> str:
    """Hash an OTP with SHA-256 so plain OTPs are never stored in the database."""
    to_hash = f"{otp}:{salt}"
    return hashlib.sha256(to_hash.encode("utf-8")).hexdigest()


def verify_otp_hash(plain_otp: str, stored_hash: str, salt: str = "") -> bool:
    """Verify plain OTP against stored SHA-256 hash."""
    calculated_hash = hash_otp(plain_otp, salt)
    return secrets.compare_digest(calculated_hash, stored_hash)


def create_access_token(
    user_id: str,
    username: str,
    email: str,
    token_version: int = 1,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Generate a signed JWT access token with user claims."""
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "email": email,
        "token_version": token_version,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
