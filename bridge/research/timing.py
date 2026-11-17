"""Deprecated: use agents.research.timing instead."""
import sys

from agents.research import timing as _impl

sys.modules[__name__] = _impl
