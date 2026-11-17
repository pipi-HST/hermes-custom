"""Deprecated: use agents.research.adapters instead."""
import sys

from agents.research import adapters as _impl

sys.modules[__name__] = _impl
