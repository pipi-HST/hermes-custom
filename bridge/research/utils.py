"""Deprecated: use agents.research.utils instead."""
import sys

from agents.research import utils as _impl

sys.modules[__name__] = _impl
