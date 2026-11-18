#!/usr/bin/env python3
"""ecat_dashboard.py - a tiny local live dashboard for an ECAT run.

Usage:
  python ecat_dashboard.py                 # auto-pick the newest run with data
  python ecat_dashboard.py .ecat           # same (walk runs_*)
  python ecat_dashboard.py .ecat/runs_XXXX # pin a run dir
  python ecat_dashboard.py --port 8765     # custom port

It is stdlib-only: a small HTTP server serves one self-refreshing page plus a JSON
API that reads the run directory (run.log, entropy_history.jsonl, gradient.json,
generator.stream.jsonl). Open the printed URL in a browser.
"""
import sys, os, json, re, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

WORKSPACE = None  # resolved from args

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def fmt_elapsed(sec):
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        sec = 0
    return "%02d:%02d" % (sec // 60, sec % 60)

def short(s, n=140):
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

def newest_run(base):
    """Return the newest runs_* dir (with at least one numeric iteration dir)."""
    if not os.path.isdir(base):
        return None
    cands = []
    for n in os.listdir(base):
        p = os.path.join(base, n)
        if os.path.isdir(p) and n.startswith('runs_'):
            if any(os.path.isdir(os.path.join(p, d)) and d.isdigit() for d in os.listdir(p)):
                cands.append(n)
    if not cands:
        return None
    return os.path.join(base, sorted(cands)[-1])

def resolve(arg):
    """arg -> run dir path (or None)."""
    base = arg
    if arg is None or arg == '' or os.path.basename(os.path.abspath(arg)) == '.ecat':
        base = os.path.abspath(arg or '.')
        if os.path.basename(base) != '.ecat' and not any(n.startswith('runs_') for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))):
            base = os.path.abspath('.')
        return newest_run(base)
    p = os.path.abspath(arg)
    if os.path.isdir(p) and any(n.startswith('runs_') for n in os.listdir(p)):
        return newest_run(p)
    if os.path.isdir(p) and os.path.basename(p).startswith('runs_'):
        return p
    # a stream file -> its grandparent is the run dir
    if os.path.isfile(p):
        d = os.path.dirname(p)
        if os.path.basename(d).isdigit():
            return os.path.dirname(d)
    return None

def iter_dirs(run_dir):
    out = []
    if not run_dir:
        return out
    for n in os.listdir(run_dir):
        p = os.path.join(run_dir, n)
        if os.path.isdir(p) and n.isdigit():
            out.append(int(n))
    return sorted(out)

def read_lines(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.readlines()
    except FileNotFoundError:
        return []

# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------

def parse_actions(run_dir, iteration, name='generator.stream.jsonl'):
    """Return a rendered action list for an iteration's stream file (generator or discriminator)."""
    out = []
    if iteration is None:
        return out
    f = os.path.join(run_dir, str(iteration), name)
    for ln in read_lines(f):
        s = ln.strip()
        if not s:
            continue
        try:
            d = json.loads(s)
        except Exception:
            continue
        t = d.get('type')
        pt = d.get('part') or {}
        el = d.get('_ecat_elapsed_s', 0)
        tag = fmt_elapsed(el)
        if t == 'text':
            txt = pt.get('text', '') or ''
            if txt.strip():
                out.append({'t': tag, 'k': 'text', 'x': txt})
        elif t == 'tool_use':
            st = pt.get('state') or {}
            inp = st.get('input') or {}
            tool = pt.get('tool', '?')
            status = st.get('status', '')
            mark = '' if status in ('completed', '') else ' (' + str(status) + ')'
            if tool == 'read':
                out.append({'t': tag, 'k': 'read', 'x': shortpath(inp.get('filePath', ''))})
            elif tool == 'write':
                c = inp.get('content') or ''
                n = c.count('\n') + 1 if c else 0
                out.append({'t': tag, 'k': 'write', 'x': shortpath(inp.get('filePath', '')) + '  (%d lines)%s' % (n, mark)})
            elif tool == 'edit':
                out.append({'t': tag, 'k': 'edit', 'x': shortpath(inp.get('filePath', '')) + mark})
            elif tool == 'bash':
                out.append({'t': tag, 'k': 'bash', 'x': short(inp.get('command', '')) + mark})
            elif tool in ('grep', 'glob'):
                out.append({'t': tag, 'k': tool, 'x': "'%s' in %s" % (short(inp.get('pattern', ''), 40), shortpath(inp.get('path', ''), 50))})
            elif tool in ('ls', 'listdir', 'list'):
                out.append({'t': tag, 'k': 'list', 'x': shortpath(inp.get('path', ''))})
            elif tool == 'task':
                out.append({'t': tag, 'k': 'task', 'x': '[%s] %s%s' % (inp.get('subagent_type', ''), short(inp.get('description', '')), mark)})
            elif tool == 'skill':
                out.append({'t': tag, 'k': 'skill', 'x': inp.get('name', '')})
            elif tool == 'todowrite':
                out.append({'t': tag, 'k': 'todo', 'x': '%d items' % len(inp.get('todos', []))})
            else:
                out.append({'t': tag, 'k': 'other', 'x': '%s %s' % (tool.upper(), short(json.dumps(inp, ensure_ascii=False), 90))})
        elif t == 'step_finish':
            tok = (pt.get('tokens') or {}).get('total')
            cost = pt.get('cost')
            parts = []
            if tok:
                parts.append('%.1fk tok' % (tok / 1000.0))
            if cost is not None:
                parts.append('$%.4f' % cost)
            out.append({'t': tag, 'k': 'step', 'x': '---- step done  ' + '  '.join(parts) + ' ----'})
    return out

def parse_history(run_dir):
    out = []
    for ln in read_lines(os.path.join(run_dir, 'entropy_history.jsonl')):
        s = ln.strip()
        if not s:
            continue
        try:
            d = json.loads(s)
        except Exception:
            continue
        out.append({'iter': d.get('iteration'), 'H': d.get('total_H'), 'breakdown': d.get('breakdown') or {}})
    return out

def parse_worklist(run_dir, iteration):
    if iteration is None:
        return []
    p = os.path.join(run_dir, str(iteration), 'gradient.json')
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        return []
    out = []
    for w in (d.get('work_list') or []):
        out.append({'file': shortpath(w.get('file', '')), 'sev': w.get('severity', ''), 'issues': [short(x, 160) for x in (w.get('issues') or [])][:3]})
    return out

def parse_phase(run_dir, iteration):
    """Return (phase, elapsed_s, tokens, cost) from run.log tail."""
    lines = read_lines(os.path.join(run_dir, 'run.log'))
    phase = 'unknown'
    elapsed = 0
    for ln in lines[-60:]:
        if 'Discriminator' in ln and 'running' in ln:
            phase = 'Discriminator'
        if 'Generator' in ln and 'running' in ln:
            phase = 'Generator'
            m = re.search(r'elapsed:\s*(\d+)m(\d+)s', ln)
            if m:
                elapsed = int(m.group(1)) * 60 + int(m.group(2))
    # iteration from last "Iteration N" line
    it = None
    for ln in reversed(lines):
        m = re.search(r'Iteration\s+(\d+)', ln)
        if m:
            it = int(m.group(1))
            break
    return phase, elapsed, it

# ---------------------------------------------------------------------------
# data assembly
# ---------------------------------------------------------------------------

def gather(arg):
    run_dir = resolve(arg)
    if not run_dir:
        return {'error': 'no run found', 'arg': arg}
    iters = iter_dirs(run_dir)
    phase, elapsed, phase_iter = parse_phase(run_dir, None)
    # current iteration = max dir with a generator or discriminator file, else max dir
    cur = iters[-1] if iters else None
    for n in reversed(iters):
        if os.path.exists(os.path.join(run_dir, str(n), 'generator.stream.jsonl')):
            cur = n
            break
    history = parse_history(run_dir)
    worklist = parse_worklist(run_dir, cur)
    actions = parse_actions(run_dir, cur)
    actions = actions[-250:]
    disc_iter = None
    for n in reversed(iters):
        if os.path.exists(os.path.join(run_dir, str(n), 'discriminator.stream.jsonl')):
            disc_iter = n
            break
    disc_actions = parse_actions(run_dir, disc_iter, 'discriminator.stream.jsonl')[-250:]
    # tokens/cost of current iter (sum step_finish)
    tok_in = tok_out = tok_total = tok_cache = 0
    cost = 0.0
    f = os.path.join(run_dir, str(cur), 'generator.stream.jsonl') if cur is not None else None
    if f and os.path.exists(f):
        for ln in read_lines(f):
            s = ln.strip()
            if not s:
                continue
            try:
                d = json.loads(s)
            except Exception:
                continue
            if d.get('type') == 'step_finish':
                pt = d.get('part') or {}
                tk = pt.get('tokens') or {}
                tok_in += tk.get('input', 0) or 0
                tok_out += tk.get('output', 0) or 0
                tok_total += tk.get('total', 0) or 0
                cache = tk.get('cache') or {}
                tok_cache += (cache.get('read', 0) or 0) + (cache.get('write', 0) or 0)
                cost += pt.get('cost', 0) or 0
    cur_breakdown = {}
    for h in history:
        if h['iter'] == cur:
            cur_breakdown = h['breakdown']
    return {
        'run': os.path.basename(run_dir),
        'run_dir': run_dir,
        'iteration': cur,
        'phase': phase,
        'elapsed_s': elapsed,
        'history': history,
        'breakdown': cur_breakdown,
        'actions': actions,
        'disc_actions': disc_actions,
        'disc_iter': disc_iter,
        'worklist': worklist,
        'tokens': {'in': tok_in, 'out': tok_out, 'total': tok_total, 'cache': tok_cache, 'cost': round(cost, 6)},
        'ts': time.strftime('%H:%M:%S'),
    }

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>ECAT Live Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,Segoe UI,sans-serif;background:#0f1115;color:#d8dee9;padding:12px;overflow-x:hidden;max-width:100vw}
h1{font-size:1rem;margin-bottom:2px}
.meta{font-size:.75rem;color:#7d8590;margin-bottom:12px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.72rem;margin-left:6px}
.disc{background:#3a2d00;color:#e3b341}.gen{background:#1f3a26;color:#56d364}.unk{background:#22262d;color:#8b949e}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{background:#161a20;border:1px solid #22262d;border-radius:8px;padding:10px 12px;margin-bottom:12px}
.card h2{font-size:.78rem;color:#8b949e;font-weight:600;margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:.78rem}
td{padding:2px 4px;border-bottom:1px solid #1c2027}
.bar{height:10px;background:#22262d;border-radius:5px;overflow:hidden}
.bar>div{height:100%;background:linear-gradient(90deg,#56d364,#e3b341)}
.feed{font-family:ui-monospace,Consolas,monospace;font-size:.72rem;max-height:30vh;overflow-y:auto;overflow-x:hidden;background:#0b0d10;border-radius:6px;padding:8px}
.feed div{padding:1px 0;white-space:normal;overflow-wrap:anywhere}
.k-read{color:#79c0ff}.k-write{color:#56d364}.k-edit{color:#d2a8ff}.k-bash{color:#ffa657}
.k-task{color:#e3b341}.k-step{color:#484f58}.k-text{color:#8b949e;font-style:italic}.k-grep,.k-glob{color:#a5d6ff}
.k-todo{color:#ff7b72}.k-other{color:#c9d1d9}
.t{color:#484f58;margin-right:6px}
.wl{font-size:.76rem;overflow-wrap:anywhere}.wl b{color:#e3b341}
.spark{width:100%;height:120px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#56d364;margin-right:4px;animation:p 1.2s infinite}
@keyframes p{50%{opacity:.3}}
</style></head><body>
<div style="display:flex;justify-content:space-between;align-items:flex-start">
<div><h1>ECAT Live <span id="run" style="color:#8b949e;font-size:.8rem"></span></h1>
<div class="meta"><span class="dot"></span><span id="phase"></span> <span id="it"></span> · elapsed <span id="el"></span> · <span id="tok"></span> · updated <span id="ts"></span></div></div>
</div>
<div class="grid">
  <div class="card"><h2>H Trend</h2><svg id="spark" class="spark" viewBox="0 0 400 120" preserveAspectRatio="none"></svg></div>
  <div class="card"><h2>Dimensions (current)</h2><table id="dims"></table></div>
</div>
<div class="card"><h2>Generator Actions (newest at bottom)</h2><div class="feed" id="feed"></div></div>
<div class="card"><h2>Discriminator Actions (newest at bottom)</h2><div class="feed" id="dfeed"></div></div>
<div class="card"><h2>Current work_list</h2><div class="wl" id="wl"></div></div>
<script>
async function tick(){
  try{
    const r=await fetch('/api/data',{cache:'no-store'}); const d=await r.json();
    if(d.error){document.getElementById('phase').textContent='no run: '+d.arg;return;}
    document.getElementById('run').textContent='— '+d.run;
    document.getElementById('phase').textContent=d.phase;
    document.getElementById('it').textContent='iter '+d.iteration;
    document.getElementById('el').textContent=(d.phase==='Generator'?Math.floor(d.elapsed_s/60)+'m'+(d.elapsed_s%60)+'s':'—');
    document.getElementById('tok').textContent='tokens '+(d.tokens.total||0).toLocaleString()+' (in '+d.tokens.in+' / out '+d.tokens.out+' / cache '+(d.tokens.cache||0).toLocaleString()+') · cost $'+d.tokens.cost;
    document.getElementById('ts').textContent=d.ts;
    // sparkline
    const H=d.history.map(x=>x.H);
    const svg=document.getElementById('spark'); svg.innerHTML='';
    if(H.length){
      const w=400,h=120,pad=6,n=H.length;
      const max=Math.max(...H,0.01), step=(w-2*pad)/Math.max(1,n-1);
      let pts=H.map((v,i)=>[pad+i*step,h-pad-(v/max)*(h-2*pad)]);
      let dpath='M'+pts.map(p=>p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' L');
      svg.innerHTML='<path d="'+dpath+'" fill="none" stroke="#56d364" stroke-width="2"/>'+
        pts.map((p,i)=>'<circle cx="'+p[0]+'" cy="'+p[1]+'" r="3" fill="#e3b341"/><text x="'+p[0]+'" y="'+(p[1]-6)+'" fill="#8b949e" font-size="9" text-anchor="middle">'+H[i].toFixed(3)+'</text>').join('');
    } else { svg.innerHTML='<text x="200" y="60" fill="#484f58" text-anchor="middle">No data yet</text>'; }
    // dims
    const b=d.breakdown||{}; const rows=Object.entries(b).filter(([k,v])=>v>0).sort((a,b)=>b[1]-a[1]);
    document.getElementById('dims').innerHTML = rows.length?rows.map(([k,v])=>
      '<tr><td style="width:38%">'+k+'</td><td><div class="bar"><div style="width:'+(v*100).toFixed(0)+'%"></div></div></td><td style="width:36px;text-align:right">'+v.toFixed(2)+'</td></tr>').join('')
      :'<tr><td>All zero or not evaluated yet</td></tr>';
    // feeds (preserve scroll; collapse long text)
    const esc = s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
    function renderFeed(elId, arr){
      const el=document.getElementById(elId);
      if(!el) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      el.innerHTML=(arr||[]).map(a=>{
        if(a.k==='text' && a.x.length>240){
          return '<div class="k-text" style="white-space:normal"><span class="t">['+a.t+']</span><details><summary>Report ('+a.x.length+' chars, click to expand)</summary><pre style="white-space:pre-wrap;margin:4px 0;color:#c9d1d9;font:inherit">'+esc(a.x)+'</pre></details></div>';
        }
        return '<div class="k-'+a.k+'"><span class="t">['+a.t+']</span>'+esc(a.x)+'</div>';
      }).join('');
      if(atBottom) el.scrollTop=el.scrollHeight;
    }
    renderFeed('feed', d.actions);
    renderFeed('dfeed', d.disc_actions);
    // worklist
    document.getElementById('wl').innerHTML=(d.worklist&&d.worklist.length)?d.worklist.map(w=>'<div><b>['+(w.sev||'?')+']</b> '+w.file+'<br><span style="color:#8b949e">'+(w.issues||[]).join(' · ')+'</span></div><br>').join(''):'<span style="color:#484f58">None</span>';
  }catch(e){document.getElementById('phase').textContent='fetch error: '+e;}
}
tick(); setInterval(tick,2000);
</script></body></html>"""

# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/api/data':
                try:
                    data = gather(self.server.arg)
                except Exception as e:
                    data = {'error': str(e), 'arg': self.server.arg}
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (ConnectionAbortedError, BrokenPipeError):
                    pass
            else:
                body = HTML.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (ConnectionAbortedError, BrokenPipeError):
                    pass
    return H

def main():
    args = [a for a in sys.argv[1:] if a]
    port = 8765
    if '--port' in args:
        i = args.index('--port'); port = int(args[i + 1]); del args[i:i + 2]
    arg = args[0] if args else None
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler())
    srv.arg = arg
    print('ECAT dashboard on http://127.0.0.1:%d  (arg=%s)' % (port, arg))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
