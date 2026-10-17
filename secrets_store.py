"""API key 的加密存储。

为什么单独一个文件：密钥只在一个地方加解密，别处拿到的永远是明文或空串，
不会出现「某处顺手 print 一下」把 key 漏进日志。

加密种子按优先级取 `APP_SECRET` → `SESSION_SECRET`。
**换了这个值，库里已存的 key 就解不开了**——换之前要在界面上重新录一遍。
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_seed = (
    os.environ.get("APP_SECRET")
    or os.environ.get("SESSION_SECRET")
    or "dev-only-insecure-seed"
).encode("utf-8")
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(_seed).digest()))


def encrypt(text: str) -> str:
    """明文 → 密文。空串原样返回（表示「没配 key」）。"""
    if not text:
        return ""
    return _fernet.encrypt(text.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """密文 → 明文。解不开（种子换了 / 数据坏了）就当空，不抛异常。"""
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
