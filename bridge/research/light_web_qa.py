"""Deprecated: use agents.research.light_web_qa instead."""
import sys

from agents.research import light_web_qa as _impl

sys.modules[__name__] = _impl
