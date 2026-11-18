#!/usr/bin/env python3
"""Task-graph view for an ECAT run: per iteration, who was asked to do what.

Four artifacts are joined per iteration:

  gradient.json            discriminator work items (file / severity / issues)
  generator.stream.jsonl   task dispatches, skill loads, file edits
  run.log                  H before/after, accepted/rejected, commit sha
  <iter>/diff.patch        which files actually landed in the commit

Because ECAT commits a whole iteration, per-task acceptance is *inferred*:
a task counts as landed when its files appear in the accepted diff.
Subagent-internal tool calls are not captured by ECAT's stream, so skill
loads are shown at iteration level, not per worker.

Usage:  python3 tools/ecat_taskgraph.py <run_dir> [--port 8767]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SKILL_NOTE = "child sessions are not captured by ECAT's stream; skill loads shown here are main-agent level"


# ---------------------------------------------------------------- parsing
def _load_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _tool_events(stream: Path):
    """Yield (tool_name, input_dict) for every tool part in a generator stream."""
    for ev in _load_jsonl(stream):
        part = ev.get("part") or {}
        if part.get("type") != "tool":
            continue
        name = part.get("tool")
        inp = (part.get("state") or {}).get("input") or {}
        if name:
            yield name, inp


def _usage_events(stream: Path) -> dict:
    """Sum main-session tokens/cost from step-finish parts of one iteration."""
    tot = {"input": 0, "output": 0, "cache_read": 0, "cost": 0.0}
    for ev in _load_jsonl(stream):
        part = ev.get("part") or {}
        if part.get("type") != "step-finish":
            continue
        tok = part.get("tokens") or {}
        tot["input"] += tok.get("input", 0) or 0
        tot["output"] += tok.get("output", 0) or 0
        tot["cache_read"] += (tok.get("cache") or {}).get("read", 0) or 0
        tot["cost"] += part.get("cost") or 0
    return tot


def _task_usage(run: Path) -> dict:
    f = run / "task_usage.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _mentioned_files(text: str) -> list[str]:
    seen = {}
    for m in re.finditer(r"([A-Za-z0-9_\-]+\.(?:ets|kt|java|ts|json5|json))", text or ""):
        seen.setdefault(m.group(1), None)
    return list(seen)


MARKUP = re.compile(r"\[/?[^\]]{0,24}\]|\x1b\[[0-9;]*m")


def _clean(line: str) -> str:
    """Strip rich/ANSI style markers such as ``Accepted[/]``."""
    return MARKUP.sub("", line)


LOG_ACCEPT = re.compile(
    r"Accepted\s+iter\s+(\d+)\s+changes:\s+H\s+([\d.]+)\s*(?:→|->)\s*([\d.]+)\s*"
    r"\(ΔH\s*=\s*([+\-][\d.]+)\)\s*\|\s*(\d+)\s+files\s+(\+\d+/\-\d+)"
)
LOG_COMMIT = re.compile(r"Committed\s+iter\s+(\d+)\s*(?:→|->)\s*([0-9a-f]{7,40})")


def _run_log_facts(run: Path) -> dict[int, dict]:
    facts: dict[int, dict] = {}
    log = run / "run.log"
    if not log.exists():
        return facts
    raw = log.read_text(encoding="utf-8", errors="ignore").splitlines()
    text = "\n".join(_clean(l) for l in raw if ("Accepted" in l or "Committed" in l))
    for m in LOG_COMMIT.finditer(text):
        facts.setdefault(int(m.group(1)), {})["commit"] = m.group(2)[:7]
    for m in LOG_ACCEPT.finditer(text):
        facts.setdefault(int(m.group(1)), {}).update(
            h_before=float(m.group(2)),
            h_after=float(m.group(3)),
            delta=float(m.group(4)),
            files_changed=int(m.group(5)),
            lines=m.group(6),
            accepted=True,
        )
    return facts


def _entropy_history(run: Path) -> dict[int, float]:
    out: dict[int, float] = {}
    for rec in _load_jsonl(run / "entropy_history.jsonl"):
        if "iteration" in rec and "total_H" in rec:
            out[int(rec["iteration"])] = round(float(rec["total_H"]), 4)
    return out


def _diff_files(iter_dir: Path) -> set[str]:
    patch = iter_dir / "diff.patch"
    if not patch.exists():
        return set()
    names = set()
    for line in patch.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("diff --git"):
            for token in line.split():
                if token.startswith("b/"):
                    names.add(Path(token[2:]).name)
    return names


def _repo_from_log(run: Path) -> Path | None:
    """The harmony repo ECAT modified, as printed in run.log."""
    log = run / "run.log"
    if not log.exists():
        return None
    m = re.search(r"Modifying harmony codebase:\s*(.+)", _clean(log.read_text(encoding="utf-8", errors="ignore")))
    if not m:
        return None
    p = Path(m.group(1).strip().strip("\ufeff"))
    return p if p.is_dir() else None


def _normalise_remote(url: str) -> str | None:
    """git@github.com:o/r.git | https://github.com/o/r.git -> https://github.com/o/r"""
    url = url.strip()
    if url.startswith("git@") and ":" in url:
        host, path = url[4:].split(":", 1)
        url = f"https://{host}/{path}"
    elif url.startswith("ssh://"):
        url = "https://" + url[6:].split("@", 1)[-1].replace(":", "/", 1) if False else url
    url = re.sub(r"\.git$", "", url.rstrip("/"))
    return url if re.match(r"https?://", url) else None


def resolve_repo(run: Path, override: str | None = None) -> str | None:
    """Base web URL of the code repo, e.g. https://github.com/owner/name."""
    if override:
        return _normalise_remote(override)
    # a one-line file next to the runs (e.g. .ecat/commit_repo.txt) pins the web repo
    for cand in (run.parent / "commit_repo.txt", run / "commit_repo.txt"):
        if cand.is_file():
            txt = cand.read_text(encoding="utf-8", errors="ignore").strip()
            if txt:
                return _normalise_remote(txt)
    repo = _repo_from_log(run)
    if repo is None:
        return None
    try:
        out = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        if not out:
            # No "origin": fall back to the first remote that has a URL, so a
            # repo that only has e.g. "gitea" still produces commit links.
            remotes = subprocess.run(["git", "-C", str(repo), "remote"],
                                     capture_output=True, text=True, timeout=20).stdout.split()
            for r in remotes:
                out = subprocess.run(["git", "-C", str(repo), "remote", "get-url", r],
                                     capture_output=True, text=True, timeout=20).stdout.strip()
                if out:
                    break
    except Exception:  # noqa: BLE001
        return None
    return _normalise_remote(out) if out else None


def _sha_map(run: Path) -> dict:
    """Old-sha -> new-sha map written after a history rewrite (see READONLY flow)."""
    for cand in (run.parent / "commit_sha_map.json", run / "commit_sha_map.json"):
        if cand.is_file():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


_PR_CACHE: dict[str, tuple[float, dict]] = {}


def _pr_index(repo_base: str | None) -> dict:
    """Map commit sha / branch -> PR info from the Gitea API.

    Cached for 60 s so a page reload does not hammer the API. Returns {} (and the
    page simply shows no PR links) when there is no token or the call fails.
    """
    if not repo_base:
        return {}
    token = os.environ.get("GITEA_TOKEN", "")
    if not token:
        return {}
    now = time.time()
    hit = _PR_CACHE.get(repo_base)
    if hit and now - hit[0] < 60:
        return hit[1]
    m = re.match(r"https?://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_base)
    if not m:
        return {}
    host, owner, repo = m.groups()
    url = f"http://{host}/api/v1/repos/{owner}/{repo}/pulls?state=all&limit=100"
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"token {token}")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode() or "[]")
    except Exception:
        return {}
    idx: dict[str, dict] = {}
    for pr in data:
        info = {
            "number": pr.get("number"),
            "url": pr.get("html_url"),
            "state": "merged" if pr.get("merged") else pr.get("state"),
            "title": pr.get("title"),
        }
        head = pr.get("head") or {}
        if head.get("sha"):
            idx[str(head["sha"])[:7]] = info
        if head.get("ref"):
            idx[f"#{head['ref']}"] = info
    _PR_CACHE[repo_base] = (now, idx)
    return idx


def load_run(run: Path, repo_base: str | None = None) -> dict:
    facts = _run_log_facts(run)
    ent = _entropy_history(run)
    usage_map = _task_usage(run)
    iterations = []
    for d in sorted([p for p in run.iterdir() if p.is_dir() and p.name.isdigit()], key=lambda p: int(p.name)):
        n = int(d.name)
        grad_path = d / "gradient.json"
        grad = json.loads(grad_path.read_text(encoding="utf-8")) if grad_path.exists() else {}
        items = grad.get("work_list") or []

        tasks, skills, edits, bashes = [], [], [], []
        main_usage = _usage_events(d / "generator.stream.jsonl")
        task_idx = -1
        for tool, inp in _tool_events(d / "generator.stream.jsonl"):
            if tool == "task":
                prompt = inp.get("prompt") or ""
                task_idx += 1
                u = usage_map.get(f"{d.name}/{task_idx}") or {}
                tasks.append(
                    {
                        "description": inp.get("description") or "(no description)",
                        "subagent_type": inp.get("subagent_type") or "general",
                        "files": _mentioned_files(prompt),
                        "prompt_len": len(prompt),
                        "mentions_skill": "skill" in prompt.lower(),
                        "usage": u or None,
                    }
                )
            elif tool == "skill":
                name = inp.get("name")
                if name and name not in skills:
                    skills.append(name)
            elif tool in ("edit", "write", "patch"):
                p = inp.get("filePath") or inp.get("file_path") or inp.get("path")
                if p and p not in edits:
                    edits.append(p)
            elif tool == "bash":
                cmd = (inp.get("command") or "")[:120]
                if cmd:
                    bashes.append(cmd)

        changed = _diff_files(d)
        worker_skills = []
        for t in tasks:
            for sk in ((t.get("usage") or {}).get("skills") or []):
                if sk not in worker_skills:
                    worker_skills.append(sk)
        task_cost = sum((t.get("usage") or {}).get("cost", 0) for t in tasks)
        task_tokens = sum(
            (t.get("usage") or {}).get("input", 0) + (t.get("usage") or {}).get("output", 0) for t in tasks
        )
        f = dict(facts.get(n, {}))
        # dir N is the evaluation of the tree as it stood when round N started:
        # its own entropy is ent[N]; the change it makes is scored as ent[N+1].
        f.setdefault("h_before", ent.get(n))
        h_after = f.get("h_after")
        # run.log is the primary source, but a rejected round logs "iter N not
        # committed" (no "Accepted ..."), which the fact parser does not match.
        # Fall back to entropy_history: round N's change is scored as ent[N+1].
        if h_after is None and ent.get(n + 1) is not None:
            h_after = ent[n + 1]
        accepted = f.get("accepted")
        if accepted is None and h_after is not None and ent.get(n) is not None:
            accepted = h_after < ent[n]

        # infer per-task landing: accepted commit contains at least one of its files
        for t in tasks:
            hit = sorted({fn for fn in t["files"] if fn in changed})
            t["changed_files"] = hit
            if accepted is None:
                t["outcome"] = "unknown"
            elif accepted and hit:
                t["outcome"] = "landed"
            elif accepted:
                t["outcome"] = "no-file-match"
            else:
                t["outcome"] = "reverted"

        # item -> task links (a task "covers" an item when it names that file)
        for it in items:
            fn = Path(str(it.get("file") or "")).name
            it["_task"] = next((i for i, t in enumerate(tasks) if fn and fn in t["files"]), None)

        # which work items never reached a task
        dispatched = {fn for t in tasks for fn in t["files"]}
        for it in items:
            fn = Path(str(it.get("file") or "")).name
            it["_dispatched"] = bool(fn) and fn in dispatched

        _commit_sha = _sha_map(run).get(str(f.get("commit") or "")[:7], f.get("commit"))
        _prs = _pr_index(repo_base)
        _pr_info = _prs.get(str(_commit_sha)[:7]) if _commit_sha else None
        if _pr_info is None:
            _pr_info = _prs.get(f"#iter/{n}")

        iterations.append(
            {
                "iter": n,
                "h_before": f.get("h_before"),
                "h_after": h_after,
                "delta": f.get("delta") if f.get("delta") is not None else (
                    (h_after - f["h_before"]) if (h_after is not None and f.get("h_before") is not None) else None
                ),
                "accepted": accepted,
                "commit": _commit_sha,
                "commit_url": (f"{repo_base}/commit/{_commit_sha}" if repo_base and _commit_sha else None),
                "pr": _pr_info,
                "files_changed": f.get("files_changed"),
                "lines": f.get("lines"),
                "items": items,
                "items_total": len(items),
                "items_high": sum(1 for i in items if i.get("severity") == "high"),
                "items_dispatched": sum(1 for i in items if i["_dispatched"]),
                "tasks": tasks,
                "skills": skills,
                "worker_skills": worker_skills,
                "main_usage": main_usage,
                "task_cost": task_cost,
                "task_tokens": task_tokens,
                "cost": round(task_cost + main_usage["cost"], 4),
                "edits": edits,
                "bashes": bashes[:6],
                "changed": sorted(changed),
            }
        )
    if iterations:
        iterations[-1]["_open"] = True
    for it in iterations:
        it["source"] = run.name
    matched = { (it.get("pr") or {}).get("number") for it in iterations }
    extra = []
    seen = set()
    for info in _pr_index(repo_base).values():
        num = info.get("number")
        if num in matched or num in seen:
            continue
        seen.add(num)
        extra.append(info)
    extra.sort(key=lambda x: x.get("number") or 0)
    return {
        "run": run.name,
        "iterations": iterations,
        "extra_prs": extra,
        "skills_total": sorted({s for i in iterations for s in i["skills"]}),
    }



# ---------------------------------------------------------------- svg tree
W = [190, 235, 290, 330]
HGAP = 44
PAD = 8
LINE_H = 12
FS = 11
CHAR_W = 6.3

KIND_STYLE = {
    "iter_ok":    ("#0d2317", "#2ea043", "#d7dae1"),
    "iter_bad":   ("#2a1110", "#f85149", "#d7dae1"),
    "iter_pend":  ("#171a21", "#3d444d", "#d7dae1"),
    "group":      ("#141821", "#30363d", "#a8b1c1"),
    "item_high":  ("#1b1214", "#f85149", "#d7dae1"),
    "item_med":   ("#1a1710", "#d29922", "#d7dae1"),
    "item_low":   ("#141821", "#30363d", "#8b93a3"),
    "item_off":   ("#12141a", "#21262d", "#6e7681"),
    "task_landed":("#0d2317", "#2ea043", "#d7dae1"),
    "task_revert":("#2a1110", "#f85149", "#d7dae1"),
    "task_unk":   ("#141821", "#30363d", "#8b93a3"),
    "skill":      ("#0e1c2b", "#1f6feb", "#c9d6e8"),
    "commit":     ("#141821", "#484f58", "#c9d6e8"),
    "dim":        ("#12141a", "#21262d", "#6e7681"),
}


def _clip(text: str, width: int) -> str:
    n = max(6, int((width - 14) / CHAR_W))
    if len(text) <= n:
        return text
    cut = text[: n - 1]
    if " " in cut[int(n * 0.6):]:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


def _node_height(node: dict) -> int:
    return 18 + LINE_H * (len(node["lines"]) - 1)


def _layout(root: dict) -> tuple[int, int, list[int]]:
    x_of = [PAD]
    for i in range(1, len(W)):
        x_of.append(x_of[i - 1] + W[i - 1] + HGAP)

    cursor = [PAD]

    def walk(node: dict, depth: int) -> float:
        node["depth"] = depth
        node["h"] = _node_height(node)
        kids = node.get("children") or []
        if not kids:
            node["y"] = cursor[0] + node["h"] / 2
            cursor[0] += node["h"] + 7
            return node["y"]
        ys = [walk(k, depth + 1) for k in kids]
        node["y"] = (ys[0] + ys[-1]) / 2
        return node["y"]

    walk(root, 0)
    return x_of[-1] + W[-1] + PAD, cursor[0] + PAD, x_of


def _svg(root: dict) -> str:
    width, height, x_of = _layout(root)
    parts = [
        f'<svg class="tree" viewBox="0 0 {width} {height:.0f}" width="{width}" '
        f'height="{height:.0f}" xmlns="http://www.w3.org/2000/svg">'
    ]

    def draw(node: dict):
        d = min(node["depth"], len(W) - 1)
        x, y, h = x_of[d], node["y"], node["h"]
        w = W[d]
        fill, stroke, fg = KIND_STYLE.get(node.get("kind"), KIND_STYLE["dim"])
        for k in node.get("children") or []:
            cx, cy = x_of[min(k["depth"], len(W) - 1)], k["y"]
            x1, x2 = x + w, cx
            parts.append(
                f'<path d="M{x1} {y:.1f} C{x1 + 22} {y:.1f}, {x2 - 22:.1f} {cy:.1f}, {x2:.1f} {cy:.1f}" '
                f'fill="none" stroke="#2b313c" stroke-width="1.3"/>'
            )
        top = y - h / 2
        link = node.get("href")
        if link:
            parts.append(f'<a href="{esc(link)}" target="_blank" rel="noopener">')
        parts.append(
            f'<rect x="{x}" y="{top:.1f}" width="{w}" height="{h}" rx="5" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"'
            + (' class="clickable"' if link else "")
            + "/>"
        )
        for n, line in enumerate(node["lines"]):
            weight = "600" if d == 0 else "400"
            colour = fg if n == 0 else "#8b93a3"
            parts.append(
                f'<text x="{x + 8}" y="{top + 14 + n * LINE_H:.1f}" fill="{colour}" font-size="{FS}" '
                f'font-weight="{weight}" font-family="ui-monospace,Consolas,monospace">{esc(_clip(line, w))}</text>'
            )
        if link:
            parts.append("</a>")
        for k in node.get("children") or []:
            draw(k)

    draw(root)
    parts.append("</svg>")
    legend = (
        '<div class="legend">'
        '<span><i style="background:#2ea043"></i>landed / accepted</span>'
        '<span><i style="background:#f85149"></i>high severity</span>'
        '<span><i style="background:#d29922"></i>medium severity</span>'
        '<span><i style="background:#30363d"></i>not dispatched (any severity)</span>'
        '<span><i style="background:#1f6feb"></i>skill suggested</span>'
        "</div>"
    )
    return "".join(parts) + legend


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


OUTCOME_LABEL = {
    "landed": "landed",
    "reverted": "reverted",
    "no-file-match": "landed · files not in diff",
    "unknown": "pending (round not scored yet)",
}


def _outcome_label(t: dict) -> str:
    return OUTCOME_LABEL.get(t.get("outcome") or "", t.get("outcome") or "?")


def _task_lines(t: dict) -> list[str]:
    lines = [t["description"], f'{t["subagent_type"]} · {_outcome_label(t)}']
    u = t.get("usage") or {}
    if u and not u.get("error"):
        bits = [f'${u.get("cost", 0):.4f}',
                f'{_fmt_tok((u.get("input") or 0) + (u.get("output") or 0))} tok',
                f'{u.get("tools", 0)} tools']
        lines.append(" · ".join(bits))
        if u.get("skills"):
            lines.append("skill: " + ", ".join(u["skills"]))
    elif u.get("error"):
        lines.append("usage: export failed")
    return lines


def _mk(lines: list[str], kind: str, children: list | None = None, href: str | None = None) -> dict:
    node = {"lines": lines, "kind": kind, "children": children or []}
    if href:
        node["href"] = href
    return node


def build_tree(it: dict, limit: int = 10) -> dict:
    acc = it["accepted"]
    kind = "iter_ok" if acc is True else ("iter_bad" if acc is False else "iter_pend")
    if it["h_before"] and it["h_after"]:
        head = [f'iter {it["iter"]}   {it.get("commit") or ""}'.rstrip(),
                f'H {it["h_before"]:.4f} → {it["h_after"]:.4f}   ΔH {it["delta"]:+.4f}']
    elif it["h_before"]:
        head = [f'iter {it["iter"]}   pending', f'H {it["h_before"]:.4f} → ?']
    else:
        head = [f'iter {it["iter"]}']

    items = sorted(it["items"], key=lambda x: {"high": 0, "medium": 1}.get(x.get("severity"), 2))
    MAXC = limit
    item_nodes = []
    for i in items[:MAXC]:
        sev = i.get("severity") or "low"
        raw = str(i.get("file") or "").strip()
        sks = i.get("suggested_skills") or []
        ti = i.get("_task")
        issue = ""
        for cand in (i.get("issues") or []):
            if cand and cand.strip():
                issue = " ".join(str(cand).split())
                break
        if raw in ("", ".", "./"):
            fn = "(repo-wide)"
        elif "." not in Path(raw).name:
            fn = Path(raw).name + "/"
        else:
            fn = Path(raw).name
        # the issue text usually repeats the path; drop it so the second line adds information
        if fn not in ("(repo-wide)",) and "." in fn:
            issue = re.sub(r"^[\w./\\:-]*" + re.escape(fn) + r"\s*[:—–-]?\s*", "", issue, count=1, flags=re.I)
        elif raw and issue.lower().startswith(raw.lower()):
            issue = issue[len(raw):].lstrip(" :—–-\t")
        lines = [f"{fn}  [{sev}]"]
        if sks:
            lines.append("skill: " + ", ".join(sks))
        elif ti is None:
            lines.append("never dispatched · " + issue[:56])
        else:
            lines.append(issue[:56] if issue else "dispatched")
        node = _mk(lines, ("item_" + ("high" if sev == "high" else "med" if sev == "medium" else "low"))
                   if ti is not None else "item_off")
        if ti is not None:
            t = it["tasks"][ti]
            ock = {"landed": "task_landed", "reverted": "task_revert"}.get(t["outcome"], "task_unk")
            node["children"].append(_mk(_task_lines(t), ock))
        item_nodes.append(node)
    if len(items) > MAXC:
        item_nodes.append(_mk([f"+{len(items) - MAXC} more work items — see “show all” above"], "dim"))

    covered = {i.get("_task") for i in it["items"] if i.get("_task") is not None}
    orphans = [
        _mk(_task_lines(t), {"landed": "task_landed", "reverted": "task_revert"}.get(t["outcome"], "task_unk"))
        for idx, t in enumerate(it["tasks"]) if idx not in covered
    ]

    roots = [
        _mk([f'discriminator · {it["items_total"]} items ({it["items_high"]} high)'], "group", item_nodes),
        _mk([f'generator · {len(it["tasks"])} dispatch'], "group",
            orphans or [_mk(["every task is attached to a work item above"], "dim")]),
        _mk(["skills (main): " + (", ".join(it["skills"]) or "none"),
             "skills (workers): " + (", ".join(it.get("worker_skills") or []) or "none")],
            "skill" if (it["skills"] or it.get("worker_skills")) else "dim"),
        _mk([f'cost ${it.get("cost", 0):.4f}',
             f'main ${it["main_usage"]["cost"]:.4f} · workers ${it.get("task_cost", 0):.4f}',
             f'{_fmt_tok(it.get("task_tokens", 0))} worker tokens'],
            "skill" if it.get("cost") else "dim"),
        _mk([f'commit {it.get("commit")}' if it.get("commit") else "commit: pending evaluation",
             (f'{len(it["changed"])} files {it.get("lines") or ""}'.strip() if it.get("commit") else "no diff yet")
             + ("" if (it.get("commit_url") or not it.get("commit")) else "  (no remote)")],
            "commit" if it.get("commit") else "dim",
            href=it.get("commit_url")),
    ]
    return _mk(head, kind, roots)


# ---------------------------------------------------------------- html
def esc(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#242833;--fg:#d7dae1;--dim:#8b93a3;
--ok:#3fb950;--bad:#f85149;--warn:#d29922;--acc:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
header{position:sticky;top:0;z-index:5;background:#12141a;border-bottom:1px solid var(--line);
padding:10px 16px;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
header h1{font-size:14px;margin:0;font-weight:600}
header .m{color:var(--dim)}
main{padding:12px 16px 40px;max-width:1400px}
section.it{background:var(--card);border:1px solid var(--line);border-radius:8px;margin-bottom:14px;overflow:hidden}
section.it>summary{cursor:pointer;padding:10px 14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;
list-style:none;background:#14171e}
section.it>summary::-webkit-details-marker{display:none}
.badge{padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid}
.ok{color:var(--ok);border-color:#1f4d2b;background:#0d2317}
.bad{color:var(--bad);border-color:#5c1f1b;background:#2a1110}
.unk{color:var(--dim);border-color:var(--line)}
.h{font-weight:600}
.delta.neg{color:var(--ok)} .delta.pos{color:var(--bad)}
ul.tree,ul.tree ul{list-style:none;margin:0;padding:0}
ul.tree ul{padding-left:20px}
ul.tree li{position:relative;padding:2px 0 2px 16px}
ul.tree li::before{content:"";position:absolute;left:0;top:0;bottom:0;border-left:1px solid var(--line)}
ul.tree li:last-child::before{bottom:auto;height:14px}
ul.tree li::after{content:"";position:absolute;left:0;top:14px;width:12px;border-top:1px solid var(--line)}
ul.tree>li{padding-left:0} ul.tree>li::before,ul.tree>li::after{display:none}
.node{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:2px 0}
.node>.t{font-weight:600}
.dim{color:var(--dim)}
.leaf{color:var(--dim);overflow-wrap:anywhere}
.tree-wrap{padding:12px 14px;border-top:1px solid var(--line);overflow-x:auto}
svg.tree{display:block;min-width:900px}
svg.tree a{text-decoration:none}
svg.tree rect.clickable{cursor:pointer}
svg.tree a:hover rect.clickable{stroke:#58a6ff;stroke-width:1.6}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:11px;margin-top:8px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.btn{margin:2px 0 8px;background:#1c2128;color:#c9d6e8;border:1px solid #30363d;border-radius:5px;
padding:3px 9px;font:11px ui-monospace,Consolas,monospace;cursor:pointer}
.btn:hover{border-color:#58a6ff;color:#fff}
.item{border-left:2px solid var(--line);padding:3px 0 3px 9px;margin-bottom:6px}
.item.high{border-left-color:var(--bad)} .item.medium{border-left-color:var(--warn)}
.item .f{color:var(--acc)}
.item .iss{color:var(--dim);overflow-wrap:anywhere}
.item.off{opacity:.45}
.task{border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin-bottom:8px;background:#12151b}
.task .d{font-weight:600}
.task .meta{color:var(--dim);margin-top:3px;overflow-wrap:anywhere}
.chip{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:0 5px;margin:2px 4px 0 0;font-size:11px}
.chip.sk{color:var(--acc);border-color:#1d3a5c;background:#0e1c2b}
.chip.f{color:var(--fg)}
.note{color:var(--dim);font-size:11px;margin-top:6px}
details.raw>summary{color:var(--dim);cursor:pointer;font-size:11px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;color:var(--dim);margin:6px 0 0;font-size:11px}
.bar{height:6px;border-radius:3px;background:#0e1c2b;position:relative;width:120px}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);border-radius:3px}
"""


def chip(text, cls=""):
    return f'<span class="chip {cls}">{esc(text)}</span>'


WITH_CAPS: dict[str, int] = {}


def merge_runs(paths: list[Path], repo_base: str | None) -> dict:
    """Load several run dirs and present them as one timeline.

    Runs carry different entropy weight sets, so H values are NOT comparable
    across them; every iteration keeps its ``source`` run name so the page can
    label it instead of implying one continuous curve.
    """
    merged: list[dict] = []
    names: list[str] = []
    for idx, p in enumerate(paths):
        d = load_run(p, repo_base)
        names.append(d["run"])
        iters = d["iterations"]
        cap = WITH_CAPS.get(str(p))
        if idx > 0 and cap is not None:
            iters = [i for i in iters if i["iter"] <= cap]
        merged.extend(iters)
    merged.sort(key=lambda i: (i["iter"], names.index(i["source"])))
    # A stopped run can leave its last round unjudged; the continuation run then
    # scores that same tree at iteration N+1. Fill the pending row from it and
    # keep the provenance, instead of silently leaving it pending forever.
    # (a) a continuation run re-measures the SAME round that the previous run left
    #     unjudged: its ent[n] is the score AFTER those changes, so it belongs to the
    #     earlier row as h_after (the earlier row still holds the real baseline).
    # (b) a stopped run's last round can also be judged by the continuation's first
    #     round at n+1 — same idea, one round later.
    # (a) same iter measured by two runs (continuation re-measures the tree the
    #     stopped run left unjudged): keep the earlier row's baseline, take the
    #     later run's ent[n] as h_after, drop the duplicate row.
    from collections import defaultdict as _dd
    drop: list[int] = []
    groups: dict[int, list[dict]] = _dd(list)
    for row in merged:
        groups[row["iter"]].append(row)
    for it, rows in groups.items():
        if len(rows) < 2:
            continue
        # earliest run first: run dir names carry the timestamp (runs_YYYYMMDD_HHMM)
        rows.sort(key=lambda r: r["source"])
        a, b = rows[0], rows[1]
        if a["source"] == b["source"] or a.get("h_after") is not None:
            continue
        if b.get("h_before") is None:
            continue
        a["h_after"] = b["h_before"]
        a["h_after_from"] = b["source"]
        if a.get("h_before") is not None:
            a["delta"] = a["h_after"] - a["h_before"]
        drop.append(id(b))
    # (b) a stopped run's last round judged by the continuation's first round (n+1)
    for i in merged:
        if id(i) in drop or i.get("h_after") is not None:
            continue
        for nxt in merged:
            if id(nxt) in drop or nxt["source"] == i["source"] or nxt.get("h_before") is None:
                continue
            if nxt["iter"] == i["iter"] + 1:
                i["h_after"] = nxt["h_before"]
                i["h_after_from"] = nxt["source"]
                break
    merged = [m for m in merged if id(m) not in drop]
    # Commits made by hand (work a stopped run left unjudged): .ecat/manual_commits.json
    # maps "<iter>" -> {commit, h_after, note}. Kept in a file so the honest source
    # is visible instead of being baked into the tool.
    for _p in paths:
        _f = _p.parent / "manual_commits.json"
        if not _f.exists():
            continue
        try:
            _recs = json.loads(_f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for _k, _rec in _recs.items():
            try:
                _n = int(_k)
            except Exception:
                continue
            for _row in merged:
                if _row["iter"] != _n:
                    continue
                if _rec.get("commit"):
                    _row["commit"] = _rec["commit"]
                if _rec.get("h_after") is not None:
                    _row["h_after"] = float(_rec["h_after"])
                    if _row.get("h_before") is not None:
                        _row["delta"] = _row["h_after"] - _row["h_before"]
                        _row["accepted"] = _row["h_after"] < _row["h_before"]
                if _rec.get("note"):
                    _row["note"] = _rec["note"]
    for i in merged:
        i["_open"] = False
    if merged:
        merged[-1]["_open"] = True
    extras: dict[int, dict] = {}
    for p in paths:
        for info in (load_run(p, repo_base).get("extra_prs") or []):
            extras.setdefault(info.get("number"), info)
    return {
        "run": " + ".join(names),
        "runs": names,
        "iterations": merged,
        "extra_prs": [extras[k] for k in sorted(extras)],
        "skills_total": sorted({s for i in merged for s in i["skills"]}),
    }


def render_iteration(it: dict) -> str:
    acc = it["accepted"]
    badge = (
        '<span class="badge ok">accepted</span>'
        if acc is True
        else ('<span class="badge bad" title="H rose: ECAT does NOT revert; the changes stay in the working tree and the next round builds on them">rejected</span>' if acc is False else '<span class="badge unk">pending</span>')
    )
    d = it.get("delta")
    delta = (
        f'<span class="delta {"neg" if d is not None and d < 0 else "pos"}">ΔH {d:+.4f}</span>'
        if d is not None
        else ""
    )
    if it["h_before"] and it["h_after"]:
        _note = (f' <span class="dim">(judged in {esc(it.get("h_after_from"))})</span>'
                 if it.get("h_after_from") else "")
        h = f'<span class="h">H {it["h_before"]:.4f} → {it["h_after"]:.4f}</span>{_note}'
    elif it["h_before"]:
        h = f'<span class="h">H {it["h_before"]:.4f} → {it["h_after"]:.4f}</span> <span class="dim">(judged in {esc(it.get("h_after_from") or "")})</span>' if it.get("h_after") is not None else f'<span class="h">H {it["h_before"]:.4f} → pending</span>'
    else:
        h = ""
    commit = f'<span class="dim">{esc(it["commit"])}</span>' if it.get("commit") else ""
    pr = it.get("pr") or {}
    pr_badge = ""
    if pr.get("url"):
        cls = "ok" if pr.get("state") == "merged" else "unk"
        pr_badge = (f'<a class="badge {cls}" href="{esc(pr["url"])}" target="_blank" '
                    f'style="text-decoration:none">PR #{pr.get("number")} {esc(pr.get("state") or "")}</a>')
    fl = f'<span class="dim">{it["files_changed"]} files {esc(it["lines"])}</span>' if it.get("files_changed") else ""

    pr_line = ""
    if pr.get("url"):
        cls = "ok" if pr.get("state") == "merged" else "unk"
        pr_line = (
            f'<div style="padding:8px 14px;border-bottom:1px solid #242833;font-size:12px">'
            f'<span class="dim">pull request</span> '
            f'<a href="{esc(pr["url"])}" target="_blank" style="color:#58a6ff;text-decoration:none">'
            f'#{pr.get("number")} {esc(pr.get("title") or "")}</a> '
            f'<span class="badge {cls}">{esc(pr.get("state") or "")}</span></div>'
        )

    total = it["items_total"]
    dispatched = it["items_dispatched"]
    tasks = it["tasks"]

    # the visual tree is built by build_tree()/_svg(); no HTML list rendering here
    return f"""
<section class="it"><details{" open" if it.get("_open") else ""}>
<summary>{badge} <b>iter {it["iter"]}</b> {h} {delta} {commit} {pr_badge} {fl}
<span class="dim" title="source run">{esc(it.get("source", ""))}</span>
<span class="dim">items {total}</span>
<span class="bar"><i style="width:{(dispatched / total * 100) if total else 0:.0f}%"></i></span>
<span class="dim">dispatched {dispatched}/{total}</span>
<span class="dim">tasks {len(tasks)}</span></summary>
{pr_line}
<div class="tree-wrap">{_svg(build_tree(it, 9999))}
  <ul class="tree"><li><span class="dim">{SKILL_NOTE}</span></li></ul>
</div></details></section>"""


def render(data: dict) -> str:
    iters = data["iterations"]
    spark = "".join(
        f'<span class="m">i{i["iter"]}:{"—" if i["h_after"] is None else round(i["h_after"], 3)}</span>'
        for i in iters
    )
    extra = "".join(
        f'<a class="badge {"ok" if p.get("state") == "merged" else "unk"}" href="{esc(p.get("url") or "")}" '
        f'target="_blank" style="text-decoration:none;margin-left:6px">PR #{p.get("number")} {esc(p.get("title") or "")}</a>'
        for p in (data.get("extra_prs") or [])
    )
    body = "".join(render_iteration(i) for i in iters) or "<p>no iterations</p>"
    sig = json.dumps(
        str(len(iters)) + ":" + ",".join(
            f'{i["iter"]}-{"p" if i.get("h_after") is None else i["h_after"]}' for i in iters
        )
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>ECAT task graph — {esc(data["run"])}</title><style>{CSS}</style></head><body>
<header><h1>ECAT task graph</h1><span class="m">{esc(data["run"])}</span>
<span class="m">skills seen: {esc(", ".join(data["skills_total"]) or "none")}</span>{spark}</header>
<main>{body}</main>
<script>
// Poll /api.json and reload only when the run actually advanced, so the page
// stays live without a fixed-interval full reload (which janks the scroll).
(function () {{
  var sig = {sig};
  function cmp(d) {{
    var it = d.iterations || [];
    return it.length + ":" + it.map(function (i) {{
      return i.iter + "-" + (i.h_after === null ? "p" : i.h_after);
    }}).join(",");
  }}
  setInterval(function () {{
    fetch("/api.json", {{cache: "no-store"}}).then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        var s = cmp(d);
        if (s !== sig) {{ location.reload(); }}
      }}).catch(function () {{}});
  }}, 20000);
}})();
</script>
</body></html>"""


# ---------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    data: dict = {}
    run_path = None
    run_paths: list = []
    repo_base: str | None = None

    def do_GET(self):  # noqa: N802
        # Reload on every request: a run is live, so a snapshot taken at
        # startup keeps showing the in-flight iteration as "pending" forever.
        if Handler.run_paths:
            Handler.data = merge_runs(Handler.run_paths, Handler.repo_base)
        elif Handler.run_path is not None:
            Handler.data = load_run(Handler.run_path, Handler.repo_base)
        if self.path.startswith("/api.json"):
            payload = json.dumps(self.data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        else:
            payload = render(self.data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # the browser navigated away / reloaded mid-write; not an error worth a traceback
            pass

    def log_message(self, *a):
        pass


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = 8767
    for a in sys.argv[1:]:
        if a.startswith("--port"):
            port = int(a.split("=", 1)[1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
    if not args:
        print(__doc__)
        sys.exit(2)
    run = Path(args[0]).resolve()
    Handler.run_path = run
    with_runs = []
    for a in sys.argv[1:]:
        if a.startswith("--with="):
            spec = a.split("=", 1)[1]
            path, _, cap = spec.partition("#")
            pr = Path(path).resolve()
            with_runs.append(pr)
            if cap:
                WITH_CAPS[str(pr)] = int(cap)
    Handler.run_paths = [run] + with_runs
    repo = None
    for a in sys.argv[1:]:
        if a.startswith("--repo="):
            repo = a.split("=", 1)[1]
    repo_base = resolve_repo(run, repo)
    Handler.repo_base = repo_base
    Handler.data = merge_runs(Handler.run_paths, repo_base)
    n = len(Handler.data["iterations"])
    print(f"serving {Handler.data['run']}: {n} iterations on http://127.0.0.1:{port}/")
    print(f"commit links: {repo_base or 'none (no git remote; pass --repo=https://github.com/o/r)'}")
    if "--open" in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
