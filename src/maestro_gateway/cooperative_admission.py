#!/usr/bin/env python3
"""P0.4.3b2f TEST-ONLY cooperative cross-process lock contract.

Not installed in MAESTRO, not a real provider cancellation acknowledgement,
not a guarantee over processes that do not cooperate. Production idle admission
is unconditionally denied; synthetic exclusive leases are for tests only.
"""
from __future__ import annotations
import contextlib
import errno
import fcntl
import os
from pathlib import Path
import stat
import time

class AdmissionBlocked(RuntimeError):
    pass


def _check_dir(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise AdmissionBlocked('unsafe or missing lock directory')
    s = root.stat(follow_symlinks=False)
    if s.st_uid != os.getuid() or (stat.S_IMODE(s.st_mode) & 0o077):
        raise AdmissionBlocked('lock directory must be owner only')


def _open_lock(root: Path) -> int:
    _check_dir(root)
    name = root / 'session.lock'
    if name.is_symlink():
        raise AdmissionBlocked('symlink lock denied')
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    if hasattr(os, 'O_CLOEXEC'):
        flags |= os.O_CLOEXEC
    old_umask = os.umask(0o077)
    try:
        fd = os.open(name, flags, 0o600)
    finally:
        os.umask(old_umask)
    try:
        s = os.fstat(fd)
        p = name.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or
                s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) & 0o077 or
                s.st_dev != p.st_dev or s.st_ino != p.st_ino):
            raise AdmissionBlocked('lock file ownership, mode or inode unsafe')
        _check_dir(root)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 3:
        raise AdmissionBlocked('invalid bounded wait')
    return float(value)


class CooperativeAdmission:
    def __init__(self, root: Path):
        self.root = Path(root)

    @contextlib.contextmanager
    def _lease(self, mode: int, wait: float = 0.0):
        deadline = time.monotonic() + _timeout(wait)
        fd = _open_lock(self.root)
        obtained = False
        try:
            while True:
                try:
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    obtained = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EINTR):
                        raise
                    if time.monotonic() >= deadline:
                        raise AdmissionBlocked('cooperative lease busy') from exc
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            yield
        finally:
            if obtained:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def foreground(self, wait: float = 0.0):
        """Shared lease. Only useful if EVERY provider admission path uses it."""
        return self._lease(fcntl.LOCK_SH, wait)

    def idle(self):
        """Never activate a real provider: no provider-side acknowledgement."""
        raise AdmissionBlocked('provider quiescence not verified; production idle denied')

    def _synthetic_exclusive_test_only(self, wait: float = 0.0):
        """ONLY for testing flock semantics; never call with a real provider."""
        return self._lease(fcntl.LOCK_EX, wait)
