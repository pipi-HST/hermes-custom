"""Deprecated: use agents.research.evidence_decisions instead."""
import sys

from agents.research import evidence_decisions as _impl

sys.modules[__name__] = _impl
