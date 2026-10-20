import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import DATA, now
from .database import get_db
from .models import User

secret_file = DATA / 'auth.key'
if not secret_file.exists():
    try:
        with secret_file.open('x', encoding='utf-8') as file:
            file.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
SECRET = secret_file.read_text(encoding='utf-8')
bearer = HTTPBearer(auto_error=False)


def hash_password(value):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 310000).hex()
    return f'{salt}:{digest}'


def verify_password(value, stored):
    salt, digest = stored.split(':')
    return hmac.compare_digest(digest, hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 310000).hex())


def create_token(user_id):
    return jwt.encode({'sub': user_id, 'exp': now() + timedelta(hours=12)}, SECRET, algorithm='HS256')


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)):
    try:
        if not credentials:
            raise ValueError()
        claims = jwt.decode(credentials.credentials, SECRET, algorithms=['HS256'])
        user = db.get(User, claims['sub'])
        if not user:
            raise ValueError()
        return user
    except (ValueError, KeyError, jwt.PyJWTError):
        raise HTTPException(401, '请先登录') from None
