#!/usr/bin/env python3
"""Expose the dashboard's latest-round report parser to the merge gate."""
import json
import sys
from harness import parse_report

if __name__ == "__main__":
    print(json.dumps(parse_report(sys.stdin.read()), ensure_ascii=False))
