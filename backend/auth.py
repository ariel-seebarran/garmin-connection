import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from fastapi import Cookie, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _fernet() -> Fernet:
    digest = hashlib.sha256(SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")


def encrypt_garmin_password(password: str) -> str:
    return _fernet().encrypt(password.encode()).decode()


def decrypt_garmin_password(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


async def get_current_user(session: str | None = Cookie(default=None)) -> int:
    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return decode_token(session)
