#!/usr/bin/env python3
"""ecat_iter_pr.py - turn one ECAT iteration into a semantic branch + PR.

Why: ECAT auto-commits `ecat: iter N | H=...`, which says nothing about what
changed. This script rewrites that commit message from the iteration's own
gradient (the Discriminator's work_list), pushes an `iter/N` branch, and opens
a pull request against main whose title is the iteration.

Usage (run from the harmony repo, after ECAT accepted an iteration):
  python tools/ecat_iter_pr.py --run <runs_dir> --iter 10 [--merge]

Env: GITEA_TOKEN (required for the PR; repo defaults to this workspace's).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_GITEA = "http://gitea.ostc"
DEFAULT_OWNER = "csong"
DEFAULT_REPO = "simple-gallery-harmonyos"


def sh(args: list[str], cwd: Path, check: bool = True) -> str:
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return (r.stdout or "") + (r.stderr or "")


def clean(text: str) -> str:
    """Strip ANSI/hvigor noise and collapse whitespace."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text or "")
    text = re.sub(r">\s*hvigor\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def entropy(n: int, run: Path) -> float | None:
    hist = run / "entropy_history.jsonl"
    if not hist.is_file():
        return None
    for line in hist.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if int(d.get("iteration", -1)) == n:
            return float(d.get("total_H"))
    return None


def compose(run: Path, n: int, commit_subject: str) -> tuple[str, str]:
    """Build (title, body) from the iteration's gradient + H delta."""
    grad = load_json(run / str(n) / "gradient.json")
    items = grad.get("work_list") or []
    issues: list[str] = []
    files: list[str] = []
    for it in items:
        f = str(it.get("file") or "")
        body = it.get("issues") or it.get("issue") or []
        if isinstance(body, str):
            body = [body]
        for b in body:
            b = clean(str(b))
            if b:
                issues.append(b)
                break
        if f and f not in files:
            files.append(f)

    build_broken = any(
        re.search(r"hvigor ERROR|CompileArkTS|ArkTS Compiler Error|10505001", i) for i in issues[:3]
    )
    h_before, h_after = entropy(n, run), entropy(n + 1, run)
    delta = (f"H {h_before:.4f} -> {h_after:.4f}" if h_before and h_after
             else (f"H {h_before:.4f} -> ?" if h_before else "H n/a"))

    if build_broken:
        title = f"iter {n}: fix ArkTS build errors"
    elif issues:
        gist = re.sub(r"^[\w./\\-]*\.(ets|kt|java)\s*:?\s+", "", issues[0])
        gist = re.sub(r"^CREATE by porting\s*", "port ", gist)
        gist = re.sub(r"\s*compare:.*$", "", gist)
        gist = gist.split(";")[0].strip()
        if len(gist) > 72:
            cut = gist[:72].rsplit(" ", 1)[0].rstrip(",;:")
            gist = cut + "…"
        stem = Path(files[0]).stem if files and files[0] not in ("", ".") else ""
        title = (f"iter {n}: {stem} — {gist}" if stem and gist
                 else f"iter {n}: " + (gist or "apply discriminator work list"))
    else:
        title = f"iter {n}: {clean(commit_subject) or 'apply discriminator work list'}"

    names = [n for n in (Path(f).name for f in files) if n]
    body_lines = [
        delta,
        f"Files: {', '.join(names[:4]) or '-'}",
    ]
    return title, "\n".join(body_lines)


def api(method: str, path: str, payload: dict | None, token: str) -> dict:
    url = f"{DEFAULT_GITEA}/api/v1{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="ECAT runs_* directory")
    ap.add_argument("--iter", type=int, required=True)
    ap.add_argument("--remote", default="gitea")
    ap.add_argument("--main", default="main")
    ap.add_argument("--merge", action="store_true", help="merge the PR after creating it")
    args = ap.parse_args()

    repo = Path.cwd()
    run = Path(args.run).resolve()
    branch = f"iter/{args.iter}"

    subject = sh(["git", "log", "-1", "--format=%s"], repo).strip().splitlines()[0]
    if not re.search(rf"iter\s+{args.iter}\b", subject):
        raise SystemExit(f"HEAD commit is not iteration {args.iter}: {subject!r}")

    title, desc = compose(run, args.iter, subject)

    # Rewrite this single commit's message (history is not yet on main).
    old_sha = sh(["git", "rev-parse", "--short", "HEAD"], repo).strip()
    sh(["git", "checkout", "-B", branch, "HEAD"], repo)
    sh(["git", "commit", "--amend", "-m", title, "-m", desc], repo)
    new_sha = sh(["git", "rev-parse", "--short", "HEAD"], repo).strip()
    # Keep the dashboard's commit links alive: ECAT recorded old_sha in run.log,
    # but that object no longer exists after the amend.
    sha_map_path = run.parent / "commit_sha_map.json"
    try:
        sha_map = json.loads(sha_map_path.read_text(encoding="utf-8")) if sha_map_path.is_file() else {}
    except Exception:
        sha_map = {}
    if old_sha != new_sha:
        sha_map[old_sha] = new_sha
        sha_map_path.write_text(json.dumps(sha_map, indent=1), encoding="utf-8")
        print(f"sha map: {old_sha} -> {new_sha}")
    sh(["git", "push", "-f", args.remote, f"{branch}:{branch}"], repo)

    token = os.environ.get("GITEA_TOKEN", "")
    if not token:
        print(f"pushed {branch}; open the PR manually: "
              f"{DEFAULT_GITEA}/{DEFAULT_OWNER}/{DEFAULT_REPO}/compare/{args.main}...{branch}")
        return

    pr = api("POST", f"/repos/{DEFAULT_OWNER}/{DEFAULT_REPO}/pulls", {
        "head": branch, "base": args.main, "title": title, "body": desc,
    }, token)
    print(f"PR #{pr.get('number')}: {pr.get('html_url')}")

    if args.merge:
        api("POST", f"/repos/{DEFAULT_OWNER}/{DEFAULT_REPO}/pulls/{pr['number']}/merge", {
            "Do": "merge", "merge_when_checks_succeed": False,
        }, token)
        print("merged into", args.main)


if __name__ == "__main__":
    main()
