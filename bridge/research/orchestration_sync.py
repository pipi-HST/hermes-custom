"""Deprecated: use agents.research.orchestration_sync instead."""
import sys

from agents.research import orchestration_sync as _impl

sys.modules[__name__] = _impl
