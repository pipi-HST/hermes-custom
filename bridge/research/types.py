"""Deprecated: use agents.research.types instead."""
import sys

from agents.research import types as _impl

sys.modules[__name__] = _impl
