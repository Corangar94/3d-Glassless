"""Bounded cross-process serialization and atomic same-directory publication."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile
import threading
import time

_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def reject_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise OSError(f"Refusing a symbolic link or reparse point: {path}")


@contextmanager
def path_lock(path: Path, timeout_s: float = 5.0):
    """Lock a stable sidecar, not the inode replaced by atomic publication.

    The sidecar is intentionally never deleted: unlinking a lock lets another
    process create a different inode and enter the same critical section.
    """
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(path.resolve()))
    with _registry_lock:
        local = _locks.setdefault(key, threading.RLock())
    if not local.acquire(timeout=timeout_s):
        raise TimeoutError(f"Timed out acquiring local file lock: {path}")
    stream = None
    locked = False
    try:
        sidecar = path.with_name(path.name + ".lock")
        reject_link(sidecar)
        stream = sidecar.open("a+b")
        if stream.seek(0, 2) == 0:
            stream.write(b"\0")
            stream.flush()
        started = time.monotonic()
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() - started >= timeout_s:
                    raise TimeoutError(f"Timed out acquiring file lock: {path}")
                time.sleep(0.025)
        yield
    finally:
        if stream is not None:
            try:
                if locked:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
        local.release()


def atomic_write(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_link(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        reject_link(path)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
