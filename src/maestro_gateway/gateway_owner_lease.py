"""P0.4.3b2i EXPERIMENTAL OS process owner lease, NOT wired to Gateway.

Protects cooperative Gateway instances from simultaneously opening the same
mission journal. Does not guarantee provider cancellation or protect external
processes ignoring the lease. Caller must hold for entire Gateway lifetime.
"""
import fcntl
import os
from pathlib import Path
import stat

class OwnerBlocked(RuntimeError):
    pass

class GatewayOwnerLease:
    def __init__(self, root):
        self.root=Path(os.path.abspath(os.fspath(root)))
        self.fd=None

    def __enter__(self):
        if self.fd is not None:
            raise OwnerBlocked('lease already active')
        if any(p.is_symlink() for p in (self.root,*self.root.parents)):
            raise OwnerBlocked('symlink in lease ancestry')
        try:
            info=self.root.lstat()
        except OSError as e:
            raise OwnerBlocked('owner directory not provisioned') from e
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)&0o077):
            raise OwnerBlocked('unsafe owner directory')
        file=self.root/'gateway-owner.lock'
        flags=os.O_RDWR|os.O_CREAT
        if hasattr(os,'O_NOFOLLOW'): flags|=os.O_NOFOLLOW
        if hasattr(os,'O_CLOEXEC'): flags|=os.O_CLOEXEC
        fd=None
        try:
            fd=os.open(file,flags,0o600)
            st=os.fstat(fd)
            fs=file.lstat()
            if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid()
                    or stat.S_IMODE(st.st_mode)&0o077 or st.st_nlink!=1
                    or st.st_dev!=fs.st_dev or st.st_ino!=fs.st_ino
                    or not stat.S_ISDIR(self.root.lstat().st_mode)):
                raise OwnerBlocked('unsafe owner lock')
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except (OSError,BlockingIOError) as e: raise OwnerBlocked('another Gateway owns journal') from e
            self.fd=fd
            return self
        except BaseException:
            if fd is not None: os.close(fd)
            raise

    def __exit__(self,*_):
        fd=self.fd
        if fd is None: return
        self.fd=None
        try: fcntl.flock(fd,fcntl.LOCK_UN)
        finally:os.close(fd)
