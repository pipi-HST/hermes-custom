#!/usr/bin/env python3
"""Start Knowledge Write Worker in daemon mode.

Usage:
    python start_worker.py                    # Use default config.yaml
    python start_worker.py --config custom.yaml
    python start_worker.py --log-level DEBUG
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from workers.knowledge_write_worker import main

if __name__ == "__main__":
    # Override default arguments to always run in daemon mode
    sys.argv.extend(["--daemon"])
    main()
