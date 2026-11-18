#!/usr/bin/env python3
"""ecat_live.py - pretty-print an ECAT generator.stream.jsonl as a live timeline.

Usage:
  python ecat_live.py <run_or_iter_or_stream>            # replay what's there
  python ecat_live.py <run_or_iter_or_stream> -f         # follow live (tail -f style)
  python ecat_live.py <path> -f --once                   # print once, no follow loop

Accepts any of:
  - a runs_XXXX dir (contains 0/ 1/ ... subdirs)      -> replays/follows all iterations
  - an iteration dir (contains generator.stream.jsonl) -> replays/follows that one
  - a direct path to a generator.stream.jsonl

Each line rendered:
  [mm:ss] .. <assistant narration>
  [mm:ss] READ  <file>
  [mm:ss] WRITE <file>  (N lines)
  [mm:ss] EDIT  <file>
  [mm:ss] BASH  <command>
  [mm:ss] GREP  'pat' in <path>
  [mm:ss] TASK  [subagent] <description>
  [mm:ss] SKILL <name>
  [mm:ss] TODO  N items
  [mm:ss] ---- step done  XXk tok  $0.0xxx ----
"""
import sys, os, json, time

def resolve(path):
    """Return (kind, base) where kind in {run, iter, stream}."""
    if os.path.isdir(path):
        if os.path.exists(os.path.join(path, 'generator.stream.jsonl')):
            return 'iter', path
        # run dir: contains numeric subdirs
        for name in os.listdir(path):
            sub = os.path.join(path, name)
            if os.path.isdir(sub) and os.path.exists(os.path.join(sub, 'generator.stream.jsonl')):
                return 'run', path
        # maybe this is the .ecat dir (or workspace) holding runs_* subdirs -> pick newest
        runs = sorted(
            [n for n in os.listdir(path)
             if n.startswith('runs_') and os.path.isdir(os.path.join(path, n))]
        )
        # prefer the newest run that actually has at least one iteration dir
        with_iter = [n for n in runs if iter_dirs(os.path.join(path, n))]
        pick = (with_iter or runs)
        if pick:
            newest = os.path.join(path, pick[-1])
            print("# auto-picked newest run: %s" % pick[-1])
            return resolve(newest)
        return 'run', path
    return 'stream', path

def iter_dirs(run_dir):
    out = []
    if not os.path.isdir(run_dir):
        return out
    for name in os.listdir(run_dir):
        p = os.path.join(run_dir, name)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, 'generator.stream.jsonl')):
            try:
                key = int(name)
            except ValueError:
                key = 10**9
            out.append((key, name, p))
    out.sort(key=lambda x: x[0])
    return out

def fmt_elapsed(sec):
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        sec = 0
    return "%02d:%02d" % (sec // 60, sec % 60)

def short(s, n=100):
    s = ' '.join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + '…'

def shortpath(p, n=70):
    p = str(p or '').replace('\\', '/')
    marker = 'telegram-migration/'
    if marker in p:
        p = p.split(marker, 1)[1]
    elif len(p.split('/')) > 3:
        p = '/'.join(p.split('/')[-3:])
    return short(p, n)

def render(d):
    t = d.get('type')
    pt = d.get('part') or {}
    el = d.get('_ecat_elapsed_s', 0)
    tag = fmt_elapsed(el)
    if t == 'text':
        txt = short(pt.get('text', ''), 150)
        return "[%s] .. %s" % (tag, txt) if txt else None
    if t == 'tool_use':
        tool = pt.get('tool', '?')
        st = pt.get('state') or {}
        inp = st.get('input') or {}
        status = st.get('status', '')
        mark = '' if status in ('completed', '') else ' (%s)' % status
        if tool == 'read':
            return "[%s] READ  %s" % (tag, shortpath(inp.get('filePath', '')))
        if tool == 'write':
            content = inp.get('content') or ''
            n = content.count('\n') + 1 if content else 0
            return "[%s] WRITE %s  (%d lines)%s" % (tag, shortpath(inp.get('filePath', '')), n, mark)
        if tool == 'edit':
            return "[%s] EDIT  %s%s" % (tag, shortpath(inp.get('filePath', '')), mark)
        if tool == 'bash':
            return "[%s] BASH  %s%s" % (tag, short(inp.get('command', '')), mark)
        if tool in ('grep', 'glob'):
            return "[%s] %s  '%s' in %s" % (tag, tool.upper(), short(inp.get('pattern', ''), 40), shortpath(inp.get('path', ''), 50))
        if tool in ('ls', 'listdir', 'list'):
            return "[%s] LIST  %s" % (tag, shortpath(inp.get('path', '')))
        if tool == 'task':
            return "[%s] TASK  [%s] %s%s" % (tag, inp.get('subagent_type', ''), short(inp.get('description', '')), mark)
        if tool == 'skill':
            return "[%s] SKILL %s" % (tag, inp.get('name', ''))
        if tool == 'todowrite':
            todos = inp.get('todos', [])
            return "[%s] TODO  %d items" % (tag, len(todos))
        return "[%s] %s %s" % (tag, tool.upper(), short(json.dumps(inp, ensure_ascii=False), 90))
    if t == 'step_finish':
        tok = (pt.get('tokens') or {}).get('total')
        cost = pt.get('cost')
        parts = []
        if tok:
            parts.append("%.1fk tok" % (tok / 1000.0))
        if cost is not None:
            parts.append("$%.4f" % cost)
        return "[%s] ---- step done  %s ----" % (tag, '  '.join(parts))
    return None

def read_lines(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.readlines()
    except FileNotFoundError:
        return []

def emit_new(lines, processed, label=None):
    """Render complete new lines; return updated processed count."""
    n = len(lines)
    out = processed
    for i in range(processed, n):
        ln = lines[i]
        # if this is the final line and lacks a trailing newline, it is still being written
        if i == n - 1 and not ln.endswith('\n'):
            break
        out = i + 1
        s = ln.strip()
        if not s:
            continue
        try:
            d = json.loads(s)
        except Exception:
            continue
        txt = render(d)
        if txt:
            print(txt, flush=True)
    return out

def main():
    args = [a for a in sys.argv[1:] if a]
    follow = '-f' in args or '--follow' in args
    once = '--once' in args
    paths = [a for a in args if not a.startswith('-')]
    if not paths:
        print(__doc__)
        sys.exit(1)
    kind, base = resolve(paths[0])
    if kind == 'stream' and not os.path.exists(base):
        print("not found: %s" % base)
        sys.exit(1)
    print("# ecat_live  target=%s  kind=%s  follow=%s" % (base, kind, follow and not once))
    print("")

    if kind in ('stream', 'iter'):
        if kind == 'iter':
            base = os.path.join(base, 'generator.stream.jsonl')
            print("=== iteration %s ===" % os.path.basename(os.path.dirname(base)))
        processed = 0
        while True:
            lines = read_lines(base)
            processed = emit_new(lines, processed)
            if not follow or once:
                break
            time.sleep(0.5)
        return

    state = {}  # name -> processed count
    current = None
    while True:
        dirs = iter_dirs(base)
        for key, name, p in dirs:
            if name not in state:
                if current != name:
                    print("=== iteration %s ===" % name, flush=True)
                    current = name
                state[name] = 0
            f = os.path.join(p, 'generator.stream.jsonl')
            lines = read_lines(f)
            new = emit_new(lines, state[name], label=name)
            state[name] = new
        if not follow or once:
            break
        time.sleep(0.5)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n# stopped")
