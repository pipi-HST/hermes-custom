"""Deprecated: use agents.research.synthesis instead."""
import sys

from agents.research import synthesis as _impl

sys.modules[__name__] = _impl
