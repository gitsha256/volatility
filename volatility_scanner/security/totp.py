"""RFC 6238 TOTP generation shared by broker integrations."""
import base64
import hashlib
import hmac
import struct
import time
from .tokens import AuthError

def generate_totp(secret: str, timestamp: float | None = None) -> str:
    try:
        normalized = secret.strip().upper().replace(" ", "").rstrip("=")
        if not normalized or len(normalized) > 256:
            raise ValueError
        key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
        if len(key) < 10:
            raise ValueError
        counter = int((time.time() if timestamp is None else timestamp) // 30)
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 15
        value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
        return f"{value % 1000000:06d}"
    except (ValueError, TypeError, struct.error):
        raise AuthError("TOTP secret must contain the Base32 setup secret, not a six-digit code or QR URL.") from None
