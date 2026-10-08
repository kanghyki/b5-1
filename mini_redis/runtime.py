"""사용자 전용 실행 디렉터리와 데몬 단일 실행을 위한 파일 잠금."""

import fcntl
import os
import stat
import tempfile
from contextlib import contextmanager


class RuntimePaths:
    def __init__(self, directory=None):
        directory = directory or os.environ.get("MINI_REDIS_RUNTIME_DIR")
        if directory is None:
            directory = os.path.join(tempfile.gettempdir(), "mini-redis-%d" % os.getuid())
        self.directory = os.path.abspath(directory)
        self.socket = os.path.join(self.directory, "redis.sock")
        if len(os.fsencode(self.socket)) > 103:
            raise ValueError("runtime directory path is too long for a Unix socket")
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        details = os.lstat(self.directory)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise ValueError("runtime directory must be owned by the current user")
        if stat.S_IMODE(details.st_mode) != 0o700:
            raise ValueError("runtime directory permissions must be 0700")

    @contextmanager
    def lock(self, name, blocking=True):
        """프로세스 종료 시 OS가 해제하는 잠금. 저장소 동시성에는 사용하지 않는다."""
        path = os.path.join(self.directory, name + ".lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
                raise ValueError("invalid runtime lock file")
            operation = fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            fcntl.flock(descriptor, operation)
            yield
        finally:
            os.close(descriptor)
