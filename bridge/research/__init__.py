"""Deprecated compatibility package: research now lives in agents.research."""
import warnings

warnings.warn(
    "bridge.research has moved to agents.research; import from agents.research instead.",
    DeprecationWarning,
    stacklevel=2,
)
