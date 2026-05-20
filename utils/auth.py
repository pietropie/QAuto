"""auth.py - JWT + bcrypt para autenticacao de usuarios."""
import os
from datetime import datetime, timedelta
import bcrypt
from jose import JWTError, jwt

SECRET_KEY  = os.getenv("SECRET_KEY", "qa-panel-insecure-change-in-production!")
ALGORITHM   = "HS256"
EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_token(email: str) -> str:
    exp = datetime.utcnow() + timedelta(days=EXPIRE_DAYS)
    return jwt.encode({"sub": email, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    """Retorna o email do token, ou lanca JWTError se invalido/expirado."""
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    email = payload.get("sub")
    if not email:
        raise JWTError("Token sem subject")
    return email
