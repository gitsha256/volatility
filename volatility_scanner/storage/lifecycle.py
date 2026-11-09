"""Prevent two writers from opening one recording directory."""
from contextlib import contextmanager
from pathlib import Path

from volatility_scanner.security.tokens import AuthError


@contextmanager
def capture_guard(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'capture.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise AuthError('Another recorder is already using this output directory') from None
        try:
            (directory / 'stop.request').unlink(missing_ok=True)
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
