#!/usr/bin/env python
"""Validate a workspace's config against its live Notion DB structure across
ALL categories (thorough drift check, off the hot path). Exits non-zero on drift.

Run: just validate [workspace]   (SYNAPSE_WORKSPACE names the stored workspace)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.business_logic import validate_all  # noqa: E402
from store import activate  # noqa: E402


def main():
    report = validate_all()
    if not report:
        print("✅ The workspace config matches the live Notion structure.")
        return
    print("⚠️  Config drift found:\n")
    for cat, issues in sorted(report.items()):
        print(f"  {cat}:")
        for issue in issues:
            print(f"    - {issue}")
    sys.exit(1)


if __name__ == "__main__":
    with activate(os.environ.get("SYNAPSE_WORKSPACE")):
        main()
