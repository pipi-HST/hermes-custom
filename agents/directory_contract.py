"""Public directory/body contract shared by Capture and Library.

These predicates validate the ``## 目录纲要`` block that Capture writes and
Library later maintains.  They used to be imported as private
``library_agent._*`` helpers by ``capture_agent``; keep this module as the
stable public seam so the implementations can move later without touching
Capture again.
"""
from __future__ import annotations

from agents.library_agent import (
    directory_absolute_url_free,
    directory_links_match_content_headings,
    has_capture_processing_note,
    has_directory_content_heading_coverage_gap,
)

__all__ = [
    "directory_absolute_url_free",
    "directory_links_match_content_headings",
    "has_capture_processing_note",
    "has_directory_content_heading_coverage_gap",
]
