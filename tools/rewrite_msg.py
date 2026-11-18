#!/usr/bin/env python3
"""git filter-branch --msg-filter helper: rewrite ECAT auto-commit messages.

Reads the old message on stdin. If it is `ecat: iter N | H=...`, emits a
semantic subject+body composed from that iteration's gradient. Anything else
passes through unchanged.

RUN dir is fixed to the run that produced those commits.
"""
import importlib.util
import re
import sys
from pathlib import Path

RUN = Path(r"C:\Users\chens\app-factory\eval\gallery-migration\.ecat\runs_20261109_1403")
TOOL = Path(r"C:\Users\chens\app-factory\tools\ecat_iter_pr.py")

spec = importlib.util.spec_from_file_location("p", TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main() -> None:
    msg = sys.stdin.read().strip()
    hit = re.match(r"ecat:\s*iter\s+(\d+)\s*\|\s*H=([0-9.]+)", msg)
    if not hit:
        print(msg)
        return
    n = int(hit.group(1))
    title, body = m.compose(RUN, n, msg)
    print(f"{title}\n\n{body}")


if __name__ == "__main__":
    main()
