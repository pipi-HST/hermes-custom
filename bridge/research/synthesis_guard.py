"""Deprecated: use agents.research.synthesis_guard instead."""
import sys

from agents.research import synthesis_guard as _impl

sys.modules[__name__] = _impl
