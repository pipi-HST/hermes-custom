"""Run theme scan and print results."""
import json
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bridge.theme_bridge import run_theme_scan

result = run_theme_scan(rescan_all=False, export_file=False)

# Output as JSON for structured parsing
print(json.dumps(result, ensure_ascii=False, indent=2))
