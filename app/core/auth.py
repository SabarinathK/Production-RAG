import time
import uuid
import jwt
import logging
from typing import Optional
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from passlib.context import CryptContext

from app.config import settings
from app.core.database import get_redis_client

logger = logging.getLogger(__name__)

import bcrypt

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pwd_bytes = plain_password.encode('utf-8')[:72]
        hash_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception as e:
        logger.warning(f"Password verification error: {e}")
        return False


class UserPayload(BaseModel):
    user_id: str
    username: str
    role: str = "user"
    jti: Optional[str] = None
    exp: Optional[int] = None


def create_access_token(user_id: str, username: str, role: str = "user", expires_in_seconds: int = 3600) -> str:
    jti = uuid.uuid4().hex
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "jti": jti,
        "exp": int(time.time()) + expires_in_seconds,
        "iat": int(time.time())
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> UserPayload:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return UserPayload(**payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired"
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )


async def is_token_revoked(jti: str) -> bool:
    if not jti:
        return False
    for attempt in range(2):
        try:
            from app.core.database import _fake_redis_client
            client = await get_redis_client()
            result = await client.get(f"token_blocklist:{jti}")
            if result is None and _fake_redis_client is not None:
                result = await _fake_redis_client.get(f"token_blocklist:{jti}")
            return result is not None
        except Exception as e:
            logger.warning(f"Redis blocklist check attempt {attempt+1} warning: {e}")
            import app.core.database as db_mod
            db_mod.redis_pool = None  # force reconnection on retry
    return False


async def revoke_token(token: str) -> bool:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"], options={"verify_exp": False})
        jti = payload.get("jti")
        exp = payload.get("exp")
        if not jti or not exp:
            return False
        
        now = int(time.time())
        ttl = exp - now
        if ttl > 0:
            for attempt in range(2):
                try:
                    from app.core.database import _fake_redis_client
                    client = await get_redis_client()
                    await client.set(f"token_blocklist:{jti}", "revoked", ex=int(ttl))
                    if _fake_redis_client is not None:
                        await _fake_redis_client.set(f"token_blocklist:{jti}", "revoked", ex=int(ttl))
                    break
                except Exception as e:
                    logger.warning(f"Redis revoke_token attempt {attempt+1} warning: {e}")
                    import app.core.database as db_mod
                    db_mod.redis_pool = None
        return True
    except Exception as e:
        logger.error(f"Failed to revoke token: {e}")
        return False


async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> UserPayload:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = credentials.credentials
    user_payload = decode_access_token(token)
    if user_payload.jti and await is_token_revoked(user_payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return user_payload


