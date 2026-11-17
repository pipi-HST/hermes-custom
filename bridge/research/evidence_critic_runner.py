"""Deprecated: use agents.research.evidence_critic_runner instead."""
import sys

from agents.research import evidence_critic_runner as _impl

sys.modules[__name__] = _impl
