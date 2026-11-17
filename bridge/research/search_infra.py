"""Deprecated: use agents.research.search_infra instead."""
import sys

from agents.research import search_infra as _impl

sys.modules[__name__] = _impl
