#!/usr/bin/env python3
"""Extract per-task (subagent session) token usage for an ECAT run.

ECAT's generator stream only carries the *main* session's tokens. Every
dispatched ``task`` part does carry the child session id, and opencode keeps
those sessions: ``opencode export <sessionID>`` returns every message with
``tokens`` / ``cost`` plus the tool calls (so we also learn which skills the
worker actually loaded).

Writes ``<run>/task_usage.json``:

    {"<iter>/<taskIndex>": {"session": "...", "agent": "...", "title": "...",
                            "input": .., "output": .., "cache_read": ..,
                            "cost": .., "tools": .., "skills": [..],
                            "files": [..], "messages": ..}}

Usage: python3 tools/ecat_task_usage.py <run_dir> [--workers 4]
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _iter_dirs(run: Path):
    return sorted([p for p in run.iterdir() if p.is_dir() and p.name.isdigit()], key=lambda p: int(p.name))


def _task_sessions(iter_dir: Path):
    """Yield (index, session_id, title) for each task dispatch in one iteration."""
    stream = iter_dir / "generator.stream.jsonl"
    if not stream.exists():
        return
    idx = 0
    for line in stream.read_text(encoding="utf-8", errors="ignore").splitlines():
        if '"tool":"task"' not in line:
            continue
        try:
            part = json.loads(line)["part"]
        except Exception:
            continue
        state = part.get("state") or {}
        meta = state.get("metadata") or {}
        sid = meta.get("sessionId") or meta.get("sessionID")
        title = state.get("title") or (state.get("input") or {}).get("description") or ""
        if not sid:
            out = state.get("output") or ""
            if 'task id="' in out:
                sid = out.split('task id="', 1)[1].split('"', 1)[0]
        if sid:
            yield idx, sid, title
        idx += 1


def export_session(sid: str) -> dict:
    try:
        raw = subprocess.run(
            ["opencode", "export", sid],
            capture_output=True,
            encoding="utf-8",
            errors="ignore",
            timeout=180,
        ).stdout
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return {"session": sid, "error": str(exc)}

    info = data.get("info") or {}
    usage = {"input": 0, "output": 0, "cache_read": 0, "cost": 0.0, "tools": 0}
    skills, files = [], []
    for msg in data.get("messages") or []:
        meta = msg.get("info") or msg
        tok = meta.get("tokens") or {}
        usage["input"] += tok.get("input", 0) or 0
        usage["output"] += tok.get("output", 0) or 0
        usage["cache_read"] += (tok.get("cache") or {}).get("read", 0) or 0
        usage["cost"] += meta.get("cost") or 0
        for p in msg.get("parts") or []:
            if p.get("type") != "tool":
                continue
            usage["tools"] += 1
            inp = (p.get("state") or {}).get("input") or {}
            if p.get("tool") == "skill" and inp.get("name") and inp["name"] not in skills:
                skills.append(inp["name"])
            if p.get("tool") in ("edit", "write", "patch"):
                f = inp.get("filePath") or inp.get("file_path") or inp.get("path")
                if f and f not in files:
                    files.append(Path(f).name)

    return {
        "session": sid,
        "agent": info.get("agent"),
        "title": info.get("title"),
        "model": (info.get("model") or {}).get("id"),
        "messages": len(data.get("messages") or []),
        **usage,
        "skills": skills,
        "files": files,
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    run = Path(args[0]).resolve()
    workers = 4
    for a in sys.argv[1:]:
        if a.startswith("--workers"):
            workers = int(a.split("=", 1)[1])

    jobs: list[tuple[str, str, str]] = []
    for d in _iter_dirs(run):
        for idx, sid, title in _task_sessions(d):
            jobs.append((f"{d.name}/{idx}", sid, title))

    target = run / "task_usage.json"
    out: dict[str, dict] = {}
    if target.exists():  # incremental: keep what we already exported
        try:
            out = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out = {}
    jobs = [j for j in jobs if j[0] not in out]
    print(f"{len(jobs)} task sessions to export from {run.name} ({len(out)} cached)")

    def work(job):
        key, sid, title = job
        rec = export_session(sid)
        rec.setdefault("title", title)
        return key, rec

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (key, rec) in enumerate(pool.map(work, jobs), 1):
            out[key] = rec
            status = "err" if rec.get("error") else f"${rec['cost']:.4f} {rec['input'] + rec['output']}tok"
            print(f"  [{i}/{len(jobs)}] iter {key}: {status} skills={rec.get('skills')}")

    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    total_cost = sum(r.get("cost", 0) for r in out.values())
    total_in = sum(r.get("input", 0) for r in out.values())
    total_out = sum(r.get("output", 0) for r in out.values())
    print(f"wrote {target}")
    print(f"tasks={len(out)} cost=${total_cost:.4f} input={total_in:,} output={total_out:,}")


if __name__ == "__main__":
    main()
