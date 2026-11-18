"""Input boundaries shared by the HTTP service and Capture extractors."""
from contextvars import ContextVar


allow_local_files: ContextVar[bool] = ContextVar("capture_allow_local_files", default=True)


def looks_like_local_path(content: str) -> bool:
    """Classify explicit paths without probing the filesystem."""
    value = (content or "").strip()
    if not value:
        return False
    if value.lower().startswith("file://") or "\x00" in value:
        return True
    if value.startswith(("/", "\\", "~", "./", "../", ".\\", "..\\")):
        return True
    return len(value) >= 2 and value[0].isalpha() and value[1] == ":"
