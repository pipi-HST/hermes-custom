"""Deprecated: use agents.research.planner instead."""
import sys

from agents.research import planner as _impl

sys.modules[__name__] = _impl
