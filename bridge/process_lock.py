"""An exclusive filesystem lock whose owner is released on process exit."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path


@contextmanager
def try_process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            # A conflicting lock is normal; permissions and other I/O errors
            # should not masquerade as another worker being active.
            if not isinstance(exc, BlockingIOError) and not (
                os.name == "nt" and exc.errno == errno.EACCES
            ):
                raise
            yield False
        else:
            yield True
        # Closing the handle releases the lock. Never unlink the lock file.
