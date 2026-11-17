"""Deprecated: use agents.research.evidence instead."""
import sys

from agents.research import evidence as _impl

sys.modules[__name__] = _impl
