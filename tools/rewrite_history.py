#!/usr/bin/env python3
"""Rewrite ECAT commit messages by rebuilding a linear history with commit-tree.

filter-branch mangles the .git path under MSYS on this machine, so we walk the
commits ourselves: same trees, same parents, new messages.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

RUN = Path(r"C:\Users\chens\app-factory\eval\gallery-migration\.ecat\runs_20261109_1403")
TOOL = Path(r"C:\Users\chens\app-factory\tools\ecat_iter_pr.py")
REF = sys.argv[1] if len(sys.argv) > 1 else "main"

spec = importlib.util.spec_from_file_location("p", TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def rewrite(msg: str) -> str:
    s = msg.strip()
    hit = re.match(r"ecat:\s*iter\s+(\d+)\s*\|", s) or re.match(r"iter\s+(\d+)\s*:", s)
    if not hit:
        return s
    title, body = m.compose(RUN, int(hit.group(1)), s)
    return f"{title}\n\n{body}"


def main() -> None:
    shas = git("rev-list", "--reverse", REF).split()
    parent = None
    new_tip = None
    for sha in shas:
        tree = git("rev-parse", f"{sha}^{{tree}}").strip()
        msg = git("log", "-1", "--format=%B", sha).strip()
        new_msg = rewrite(msg)
        # preserve author/committer dates so the timeline does not lie
        env_date = git("log", "-1", "--format=%aI", sha).strip()
        args = ["commit-tree", tree]
        if parent:
            args += ["-p", parent]
        args += ["-m", new_msg]
        out = subprocess.run(["git", *args], capture_output=True, text=True,
                             encoding="utf-8", env={"GIT_AUTHOR_DATE": env_date,
                                                    "GIT_COMMITTER_DATE": env_date,
                                                    **{k: v for k, v in __import__("os").environ.items()}})
        if out.returncode != 0:
            raise SystemExit(out.stderr)
        parent = out.stdout.strip()
        new_tip = parent
        print(f"{sha[:7]} -> {new_tip[:7]}  {new_msg.splitlines()[0][:70]}")
    git("update-ref", f"refs/heads/{REF}", new_tip)
    print("updated", REF, "->", new_tip)


if __name__ == "__main__":
    main()
