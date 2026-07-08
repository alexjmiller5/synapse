#!/usr/bin/env python
"""Validate databases.yaml against the live Notion DB structure across ALL
categories (thorough drift check, off the hot path). Exits non-zero on drift.

Run: op run --env-file=.env.tpl -- uv run scripts/validate_config.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from core.business_logic import validate_all  # noqa: E402


def main():
    report = validate_all()
    if not report:
        print("✅ databases.yaml matches the live Notion structure.")
        return
    print("⚠️  Config drift found:\n")
    for cat, issues in sorted(report.items()):
        print(f"  {cat}:")
        for issue in issues:
            print(f"    - {issue}")
    sys.exit(1)


if __name__ == "__main__":
    main()
