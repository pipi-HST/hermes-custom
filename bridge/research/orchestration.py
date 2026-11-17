"""Deprecated: use agents.research.orchestration instead."""
import sys

from agents.research import orchestration as _impl

sys.modules[__name__] = _impl
