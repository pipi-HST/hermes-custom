"""Deprecated: use agents.research.evidence_critic_io instead."""
import sys

from agents.research import evidence_critic_io as _impl

sys.modules[__name__] = _impl
