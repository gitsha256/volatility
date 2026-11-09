"""Shared credential errors, token expiry hints and atomic token storage."""
from __future__ import annotations
import base64
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from dotenv import set_key

class AuthError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


_lock = threading.RLock()
def token_expiry(token: str | None) -> float | None:
    try:
        if not token or len(token) > 65536:
            return None
        payload = token.split(".")[1]
        expiry = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp")
        if isinstance(expiry, (int, float)) and not isinstance(expiry, bool) and math.isfinite(expiry):
            return float(expiry)
    except (ValueError, IndexError, AttributeError, TypeError):
        pass
    return None


def save_tokens(env_file: Path, tokens: dict, *, broker: str = "FYERS", aliases=()) -> None:
    """Replace token keys together, preserving other settings and existing file on failure."""
    brokers = tuple(dict.fromkeys((broker, *aliases)))
    if set(brokers) - {"FYERS", "UPSTOX", "UPSTOX_ALGO"}:
        raise ValueError("Unsupported broker")
    with _lock:
        temporary = None
        try:
            text = env_file.read_text(encoding="utf-8-sig") if env_file.exists() else ""
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", dir=env_file.parent,
                                             prefix=".env.", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
            for prefix in brokers:
                for name in ("access_token", "refresh_token"):
                    value = tokens.get(name)
                    if isinstance(value, str) and value.strip():
                        set_key(temporary, prefix + "_" + name.upper(), value, encoding="utf-8-sig")
            os.replace(temporary, env_file)
        except OSError:
            raise AuthError("Could not save renewed tokens to .env. Existing configuration was preserved.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        for prefix in brokers:
            for name in ("access_token", "refresh_token"):
                key = prefix + "_" + name.upper()
                if key in os.environ and isinstance(tokens.get(name), str) and tokens[name].strip():
                    os.environ[key] = tokens[name]
