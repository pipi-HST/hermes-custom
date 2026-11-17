"""Deprecated: use agents.research.source_filter instead."""
import sys

from agents.research import source_filter as _impl

sys.modules[__name__] = _impl
