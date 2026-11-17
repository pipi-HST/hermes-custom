"""Deprecated: use agents.research.evidence_classification instead."""
import sys

from agents.research import evidence_classification as _impl

sys.modules[__name__] = _impl
