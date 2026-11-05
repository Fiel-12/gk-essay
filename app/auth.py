"""认证：口令哈希、token 签发/校验、当前用户依赖。

为什么不用 passlib / python-jose：
1. 规格限定依赖，标准库 hashlib + hmac + base64 完全够用。
2. pbkdf2_hmac 是「慢哈希」，盐随机、迭代次数高，能挡住离线字典攻击；
   token 用 HMAC-SHA256 签名 + 过期时间，服务端无需存会话状态。
3. verify 一律走 hmac.compare_digest，避免时序侧信道。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

from fastapi import Header, HTTPException

from . import db
from .config import settings
from .schemas import UserOut

# 盐的字节数与迭代次数：16 字节随机盐足够让同一口令产生不同哈希
_SALT_BYTES = 16
_ITERATIONS = 120_000
# token 有效期：实验/演示项目给 30 天，避免频繁登录打断演示
_TOKEN_TTL = 30 * 24 * 3600


def hash_password(password: str) -> tuple[str, str]:
    """返回 (哈希十六进制, 盐十六进制)。盐每次随机，重复注册也不产生相同密文。"""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """用相同参数重算一遍再比对；salt 非法时直接判失败而不是抛异常。"""
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(dk.hex(), hash_hex)


def _b64url_encode(raw: bytes) -> str:
    """URL 安全的 base64，去掉填充，token 里不含 / + = 等特殊字符，方便放进 header。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    """补回被去掉的填充再解码，容错长度不是 4 倍数的情形。"""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(user_id: int) -> str:
    """签一个「payload.签名」的 token，payload 里带过期时间。"""
    payload = f"{user_id}:{int(time.time()) + _TOKEN_TTL}"
    encoded = _b64url_encode(payload.encode("utf-8"))
    sig = hmac.new(settings.secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def verify_token(token: str) -> int | None:
    """校验签名与有效期，成功返回 user_id，否则 None。"""
    try:
        encoded, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(settings.secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = _b64url_decode(encoded).decode("utf-8")
        user_id_s, exp_s = payload.split(":", 1)
        if int(exp_s) < time.time():
            return None
        return int(user_id_s)
    except (ValueError, UnicodeDecodeError):
        return None


async def get_current_user(authorization: str = Header(default="")) -> UserOut:
    """FastAPI 依赖：从 Bearer 头解出当前用户，失败统一 401。

    这里把「解 token + 查库」都做了，端点只需声明 Depends(get_current_user)，
    就能拿到 UserOut，既省样板又保证每个受保护接口口径一致。
    """
    token = ""
    if authorization:
        # 兼容大小写与多个空格，客户端只要按约定发 Bearer 头即可
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    user_id = verify_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user: dict[str, Any] | None = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return UserOut(
        id=user["id"],
        username=user["username"],
        nickname=user.get("nickname") or None,
        created_at=user["created_at"],
    )


__all__ = [
    "hash_password",
    "verify_password",
    "sign_token",
    "verify_token",
    "get_current_user",
]
