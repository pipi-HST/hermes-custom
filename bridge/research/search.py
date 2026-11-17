"""Deprecated: use agents.research.search instead."""
import sys

from agents.research import search as _impl

sys.modules[__name__] = _impl
