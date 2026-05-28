# Hermes agent-main Phase 7 overlay

This checkpoint preserves the external Hermes files changed during Phase 7.

`/Users/pipi/Downloads/hermes-agent-main` is not a Git checkout on this machine, so the live gateway changes cannot be committed from that directory directly. The files in this folder are an overlay copy with the same relative paths:

- `agent/hermes_custom_envelope_adapter.py`
- `agent/tool_executor.py`
- `tools/hermes_custom_envelope.py`
- `tools/capture_tool.py`
- `tools/commit_capture_draft_tool.py`
- `tools/revise_capture_draft_tool.py`
- `tools/refine_capture_tool.py`
- `tools/search_notes_tool.py`
- `tools/theme_scan_tool.py`
- `tools/graph_report_tool.py`
- `tools/daily_review_tool.py`
- `tools/synthesize_garden_tool.py`

Restore onto the current local Hermes checkout with:

```bash
cp -R docs/checkpoints/2026-05-29-phase7-hermes-agent-main-overlay/agent/. /Users/pipi/Downloads/hermes-agent-main/agent/
cp -R docs/checkpoints/2026-05-29-phase7-hermes-agent-main-overlay/tools/. /Users/pipi/Downloads/hermes-agent-main/tools/
```

After restoring, verify with:

```bash
PYTHONPATH=/Users/pipi/hermes-custom /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py -q -k 'hermes'
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m py_compile agent/hermes_custom_envelope_adapter.py agent/tool_executor.py tools/hermes_custom_envelope.py tools/capture_tool.py tools/commit_capture_draft_tool.py tools/revise_capture_draft_tool.py tools/refine_capture_tool.py tools/search_notes_tool.py tools/theme_scan_tool.py tools/graph_report_tool.py tools/daily_review_tool.py tools/synthesize_garden_tool.py
```
