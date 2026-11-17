"""Deprecated: use agents.research.extraction instead."""
import sys

from agents.research import extraction as _impl

sys.modules[__name__] = _impl
