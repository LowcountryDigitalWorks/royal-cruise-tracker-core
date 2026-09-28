#!/usr/bin/env python3
"""Classify already-sanitized tracker output without retaining Royal payload data."""
from __future__ import annotations

import re
import sys
from pathlib import Path


HTTP_400 = re.compile(r"HTTP(?:[^0-9]| Error )*400|400 Client Error|Bad Request", re.I)
HTTP_403 = re.compile(r"HTTP(?:[^0-9]| Error )*403|403 Client Error|Access Denied", re.I)
HTTP_429 = re.compile(r"HTTP(?:[^0-9]| Error )*429|429 Client Error|Too Many Requests", re.I)
HTTP_5XX = re.compile(r"HTTP(?:[^0-9]| Error )*5[0-9][0-9]|5[0-9][0-9] Server Error", re.I)
NETWORK = re.compile(r"timed out|timeout|connection error|connection reset", re.I)


def classify(text: str) -> str:
    if HTTP_400.search(text):
        return "royal_http_400"
    if HTTP_403.search(text):
        return "royal_http_403"
    if HTTP_429.search(text):
        return "royal_rate_limited"
    if HTTP_5XX.search(text):
        return "royal_server_error"
    if NETWORK.search(text):
        return "royal_network_failure"
    return "royal_checker_failure"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: classify_tracker_failure.py <sanitized-log-file>")
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    print(classify(text))


if __name__ == "__main__":
    main()
