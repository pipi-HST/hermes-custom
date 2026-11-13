"""Sanitising helpers for text that is embedded into an LLM prompt.

Only **untrusted content** may be passed through :func:`sanitize_untrusted_text`:
retrieved web pages, search snippets, extracted page bodies, imported notes and
tool output.  Never run system prompts, answer/evidence contracts, planner JSON
or any other structured instruction payload through it — escaping their angle
brackets would corrupt the delimiters and JSON the model is supposed to parse.

The sanitiser does three things, in order:

1. removes confirmation phrases (defence in depth on top of the rule that only
   the raw user turn may confirm a durable write);
2. escapes ``<`` and ``>`` as ``&lt;`` / ``&gt;`` so untrusted text cannot close
   a ``</source>`` (or any other) delimiter and forge a system-looking section;
3. truncates to ``limit`` characters when one is given.
"""
from __future__ import annotations

from bridge.confirmation import redact_confirmation_phrases


def sanitize_untrusted_text(text: object, *, limit: int | None = None) -> str:
    """Make one piece of untrusted content safe to embed in a prompt.

    ``limit`` truncates the result to at most that many characters (``None``
    keeps the full text).  The value is coerced with ``str`` so callers can pass
    fields straight from loosely typed payloads.
    """
    value = redact_confirmation_phrases(str(text if text is not None else ""))
    value = value.replace("<", "&lt;").replace(">", "&gt;")
    if limit is not None and limit >= 0 and len(value) > limit:
        value = value[:limit]
    return value
