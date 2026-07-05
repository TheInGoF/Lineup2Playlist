#!/usr/bin/env python3
"""
Lineup2Playlist - web interface
===============================

Local graphical interface in the browser, started from the terminal:

    python festival_cli.py -w          # http://localhost:666
    python festival_cli.py -w 6660     # different port

Binds ONLY to 127.0.0.1 (no network access). Uses nothing but the Python
standard library - no extra dependency.

Same features as the terminal menu: choose a line-up, set target/mode/
options, preview, start with live progress (Server-Sent Events) including the
TIDAL login link in the browser, and a view of the manual tasks.
"""

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import festival_playlist as fp
import festival_cli as fc

DEFAULT_PORT = 666
HOST = "127.0.0.1"


# ---------------------------------------------------------------------------
# RUN LOG (thread-safe, for SSE streaming to the browser)
# ---------------------------------------------------------------------------

class RunLog:
    """Collects the events of a run and wakes waiting SSE connections."""

    def __init__(self):
        self.events = []          # list of {type: log|error|done, text}
        self.active = False
        self.cv = threading.Condition()

    def start(self):
        with self.cv:
            self.events = []
            self.active = True
            self.cv.notify_all()

    def emit(self, etype, text):
        with self.cv:
            self.events.append({"type": etype, "text": text})
            self.cv.notify_all()

    def finish(self):
        with self.cv:
            # Completion event so the browser recognizes the run as finished
            # (re-enable the button, reload tasks) and closes the stream.
            self.events.append({"type": "done", "text": "done"})
            self.active = False
            self.cv.notify_all()

    def snapshot_from(self, idx, timeout=15):
        """Wait for new events from index idx; return (new, active)."""
        with self.cv:
            if idx >= len(self.events) and self.active:
                self.cv.wait(timeout=timeout)
            return self.events[idx:], self.active


RUNLOG = RunLog()
_run_lock = threading.Lock()   # only one run at a time


class _StreamToLog:
    """Replaces sys.stdout/stderr during a run and forwards each line to the
    RunLog (while mirroring to the real terminal)."""

    def __init__(self, runlog, mirror):
        self.runlog = runlog
        self.mirror = mirror
        self.buf = ""

    def write(self, s):
        try:
            self.mirror.write(s)
        except Exception:
            pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.runlog.emit("log", line)

    def flush(self):
        try:
            self.mirror.flush()
        except Exception:
            pass

    def close_line(self):
        if self.buf:
            self.runlog.emit("log", self.buf)
            self.buf = ""


def _headless_run(cfg):
    """A complete run without terminal interaction (output -> RunLog)."""
    fp.PLEX_BASEURL = cfg["plex_baseurl"]
    fp.PLEX_TOKEN = cfg["plex_token"]
    fp.PLEX_LIBRARY = cfg["plex_library"]

    genres, bands = fp.parse_lineup(cfg["lineup"])
    tasks = fp.TaskLog()
    collected = None
    try:
        session = fp.tidal_login()
        collected = fp.collect(session, bands, genres, cfg["top"],
                               cfg["catalog"], tasks)
        if cfg["dry_run"]:
            print("Dry run: no playlist created.")
        elif not collected:
            print("No tracks collected - no playlist created.")
        elif cfg["target"] == "tidal":
            fp.build_tidal_playlist(session, collected, cfg["name"], cfg["catalog"])
        else:
            fp.build_plex_playlist(collected, cfg["name"], tasks)
    finally:
        if collected is not None or tasks.has_tasks():
            tasks.write(fp.TASK_FILE)
            if tasks.has_tasks():
                print(f"{tasks.count()} open manual task(s) - see below.")
            else:
                print("All done automatically - no open tasks.")


def _run_worker(cfg):
    old_out, old_err = sys.stdout, sys.stderr
    out = _StreamToLog(RUNLOG, old_out)
    err = _StreamToLog(RUNLOG, old_err)
    sys.stdout, sys.stderr = out, err
    try:
        _headless_run(cfg)
    except SystemExit as e:
        RUNLOG.emit("error", f"Aborted: {e}")
    except Exception as e:
        RUNLOG.emit("error", f"Error: {type(e).__name__}: {e}")
    finally:
        out.close_line()
        err.close_line()
        sys.stdout, sys.stderr = old_out, old_err
        RUNLOG.finish()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _effective_config():
    """Load the config and enrich it with runtime info."""
    cfg = fc.load_config()
    files = fc.find_lineup_files()
    if not cfg["lineup"] and len(files) == 1:
        cfg["lineup"] = files[0]
        fc._suggest_name(cfg)
    return cfg, files


def _lineup_info(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        genres, bands = fp.parse_lineup(path, verbose=False)
    except SystemExit:
        return {"error": "unreadable"}
    return {"genres": genres, "bands": bands}


# ---------------------------------------------------------------------------
# HTTP HANDLER
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Lineup2Playlist/1.0"

    def log_message(self, *_):
        pass  # don't spam the (possibly redirected) stdout with requests

    # ---- response helpers ----------------------------------------------

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    # ---- routing --------------------------------------------------------

    def do_GET(self):
        route = urlparse(self.path)
        path, query = route.path, parse_qs(route.query)
        if path == "/":
            self._send_html(PAGE)
        elif path == "/api/state":
            self._api_state()
        elif path == "/api/preview":
            self._api_preview(query.get("lineup", [None])[0])
        elif path == "/api/tasks":
            self._api_tasks()
        elif path == "/api/stream":
            self._api_stream()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            self._api_save_config(self._read_json())
        elif path == "/api/run":
            self._api_run(self._read_json())
        else:
            self._send_json({"error": "not found"}, 404)

    # ---- API ------------------------------------------------------------

    def _api_state(self):
        cfg, files = _effective_config()
        self._send_json({
            "config": cfg,
            "lineups": [{"path": p, "name": os.path.basename(p)} for p in files],
            "plex_ready": fc.plex_ready(cfg),
            "running": RUNLOG.active,
        })

    def _api_preview(self, lineup):
        self._send_json({"lineup": lineup, "info": _lineup_info(lineup)})

    def _api_tasks(self):
        text = ""
        if os.path.exists(fp.TASK_FILE):
            with open(fp.TASK_FILE, encoding="utf-8") as f:
                text = f.read()
        self._send_json({"text": text})

    def _api_save_config(self, data):
        cfg = fc.load_config()
        for key in fc.DEFAULTS:
            if key in data:
                cfg[key] = data[key]
        if cfg["lineup"] and not os.path.isfile(cfg["lineup"]):
            cfg["lineup"] = None
        fc.save_config(cfg)
        self._send_json({"ok": True, "config": cfg})

    def _api_run(self, data):
        cfg = fc.load_config()
        for key in fc.DEFAULTS:
            if key in data:
                cfg[key] = data[key]

        # Validation with immediate feedback (before the long collection phase)
        if not cfg["lineup"] or not os.path.isfile(cfg["lineup"]):
            return self._send_json({"error": "No valid line-up file selected."}, 400)
        if cfg["target"] == "plex" and not cfg["dry_run"]:
            if not fc.plex_ready(cfg):
                return self._send_json(
                    {"error": "Plex credentials missing (server URL/token)."}, 400)
            try:
                import plexapi  # noqa: F401
            except ImportError:
                return self._send_json(
                    {"error": "The 'plexapi' package is not installed."}, 400)

        if not _run_lock.acquire(blocking=False):
            return self._send_json({"error": "A run is already in progress."}, 409)

        fc.save_config(cfg)
        RUNLOG.start()

        def worker():
            try:
                _run_worker(cfg)
            finally:
                _run_lock.release()

        threading.Thread(target=worker, daemon=True).start()
        self._send_json({"ok": True})

    def _api_stream(self):
        """Server-Sent Events: streams run output live to the browser."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        idx = 0
        try:
            while True:
                events, active = RUNLOG.snapshot_from(idx)
                for ev in events:
                    idx += 1
                    payload = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                if not events:
                    self.wfile.write(b": ping\n\n")  # heartbeat
                    self.wfile.flush()
                if not active and idx >= len(RUNLOG.events):
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser closed the connection


# ---------------------------------------------------------------------------
# SINGLE-PAGE INTERFACE
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lineup2Playlist</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
         background: #14151a; color: #e8e8ea; line-height: 1.5; }
  header { padding: 20px 24px; background: #1d3b2f;
           border-bottom: 1px solid #2a2c34; }
  header h1 { margin: 0; font-size: 20px; }
  header p { margin: 4px 0 0; color: #9aa0aa; font-size: 13px; }
  main { max-width: 900px; margin: 0 auto; padding: 24px;
         display: grid; gap: 20px; grid-template-columns: 1fr 1fr; }
  .card { background: #1b1c22; border: 1px solid #2a2c34; border-radius: 10px;
          padding: 18px; }
  .card.wide { grid-column: 1 / -1; }
  h2 { margin: 0 0 12px; font-size: 14px; text-transform: uppercase;
       letter-spacing: .05em; color: #9aa0aa; }
  label { display: block; margin: 10px 0 4px; font-size: 13px; color: #c4c8d0; }
  input[type=text], input[type=number], select {
    width: 100%; padding: 8px 10px; background: #24262e; color: #e8e8ea;
    border: 1px solid #343742; border-radius: 6px; font-size: 14px; }
  .row { display: flex; gap: 8px; }
  .seg { display: inline-flex; border: 1px solid #343742; border-radius: 6px;
         overflow: hidden; }
  .seg button { background: #24262e; color: #c4c8d0; border: none;
       padding: 8px 14px; cursor: pointer; font-size: 13px; }
  .seg button.on { background: #2f7d5b; color: #fff; }
  .check { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
  .check input { width: auto; }
  button.go { width: 100%; padding: 12px; background: #2f7d5b; color: #fff;
     border: none; border-radius: 8px; font-size: 15px; font-weight: 600;
     cursor: pointer; margin-top: 8px; }
  button.go:disabled { background: #3a3d47; color: #7a7f8a; cursor: default; }
  .bands { max-height: 220px; overflow-y: auto; font-size: 13px;
           columns: 2; column-gap: 16px; }
  .bands div { break-inside: avoid; padding: 1px 0; color: #c4c8d0; }
  .muted { color: #9aa0aa; font-size: 13px; }
  .chip { display: inline-block; background: #24262e; border: 1px solid #343742;
    border-radius: 12px; padding: 2px 10px; margin: 2px 2px 0 0; font-size: 12px; }
  #log { background: #0e0f13; border: 1px solid #2a2c34; border-radius: 8px;
    padding: 12px; height: 260px; overflow-y: auto; font-family:
    ui-monospace, Menlo, monospace; font-size: 12.5px; white-space: pre-wrap;
    word-break: break-word; }
  #log .err { color: #ff8f8f; }
  #log .login { color: #7fd0ff; }
  #log a { color: #7fd0ff; }
  .plexbox { display: none; }
  .plexbox.show { display: block; }
  #tasks { white-space: pre-wrap; font-family: ui-monospace, Menlo, monospace;
    font-size: 12.5px; color: #c4c8d0; max-height: 200px; overflow-y: auto; }
  @media (max-width: 720px) { main { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>Lineup2Playlist</h1>
  <p>Turn a festival line-up into a ready-to-play TIDAL or Plex playlist.</p>
</header>
<main>
  <section class="card">
    <h2>Line-up</h2>
    <label>File</label>
    <select id="lineup"></select>
    <div id="genres" style="margin-top:10px"></div>
  </section>

  <section class="card">
    <h2>Settings</h2>
    <label>Target</label>
    <div class="seg" id="target">
      <button data-v="tidal">TIDAL playlist</button>
      <button data-v="plex">Plex matching</button>
    </div>
    <label>Collection mode</label>
    <div class="seg" id="mode">
      <button data-v="top">Top tracks</button>
      <button data-v="catalog">Catalog (all albums)</button>
    </div>
    <div class="row" style="margin-top:4px">
      <div style="flex:0 0 120px">
        <label>Songs/band</label>
        <input type="number" id="top" min="1" max="50">
      </div>
      <div style="flex:1">
        <label>Playlist name</label>
        <input type="text" id="name">
      </div>
    </div>
    <div class="check">
      <input type="checkbox" id="dry"><label for="dry" style="margin:0">
      Dry run (only collect, don't create a playlist)</label>
    </div>
  </section>

  <section class="card wide plexbox" id="plexbox">
    <h2>Plex settings</h2>
    <div class="row">
      <div style="flex:1"><label>Server URL</label>
        <input type="text" id="plex_baseurl" placeholder="http://192.168.1.10:32400"></div>
      <div style="flex:1"><label>Music library</label>
        <input type="text" id="plex_library"></div>
    </div>
    <label>Token</label>
    <input type="text" id="plex_token">
  </section>

  <section class="card wide">
    <h2>Bands</h2>
    <div class="bands" id="bands"><span class="muted">-</span></div>
  </section>

  <section class="card wide">
    <button class="go" id="run">Build playlist</button>
    <div id="status" class="muted" style="margin-top:8px"></div>
  </section>

  <section class="card wide">
    <h2>Live progress</h2>
    <div id="log"><span class="muted">No run started yet.</span></div>
  </section>

  <section class="card wide">
    <h2>Manual tasks</h2>
    <div id="tasks"><span class="muted">-</span></div>
  </section>
</main>
<script>
let cfg = {};
const $ = id => document.getElementById(id);

function linkify(text) {
  const div = document.createElement('span');
  const re = /(https?:\/\/[^\s]+)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    div.appendChild(document.createTextNode(text.slice(last, m.index)));
    const a = document.createElement('a');
    a.href = m[0]; a.textContent = m[0]; a.target = '_blank'; a.rel = 'noopener';
    div.appendChild(a);
    last = m.index + m[0].length;
  }
  div.appendChild(document.createTextNode(text.slice(last)));
  return div;
}

function setSeg(groupId, value) {
  [...$(groupId).children].forEach(b =>
    b.classList.toggle('on', b.dataset.v === value));
}

function applyConfig(c) {
  cfg = c;
  const sel = $('lineup');
  setSeg('target', c.target);
  setSeg('mode', c.catalog ? 'catalog' : 'top');
  $('top').value = c.top;
  $('name').value = c.name;
  $('dry').checked = !!c.dry_run;
  $('plex_baseurl').value = c.plex_baseurl || '';
  $('plex_library').value = c.plex_library || '';
  $('plex_token').value = c.plex_token || '';
  $('plexbox').classList.toggle('show', c.target === 'plex');
  if (c.lineup) sel.value = c.lineup;
  preview();
}

async function loadState() {
  const s = await (await fetch('/api/state')).json();
  const sel = $('lineup');
  sel.innerHTML = '';
  if (!s.lineups.length) {
    const o = document.createElement('option');
    o.textContent = 'no line-up file found'; o.value = '';
    sel.appendChild(o);
  }
  s.lineups.forEach(l => {
    const o = document.createElement('option');
    o.value = l.path; o.textContent = l.name;
    sel.appendChild(o);
  });
  applyConfig(s.config);
  loadTasks();
  if (s.running) startStream();
}

function currentConfig() {
  return {
    lineup: $('lineup').value || null,
    target: [...$('target').children].find(b => b.classList.contains('on')).dataset.v,
    catalog: [...$('mode').children].find(b => b.classList.contains('on')).dataset.v === 'catalog',
    top: parseInt($('top').value) || 10,
    name: $('name').value,
    dry_run: $('dry').checked,
    plex_baseurl: $('plex_baseurl').value,
    plex_token: $('plex_token').value,
    plex_library: $('plex_library').value,
  };
}

let saveTimer = null;
function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    await fetch('/api/config', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(currentConfig())});
  }, 400);
}

async function preview() {
  const lineup = $('lineup').value;
  const bands = $('bands'), genres = $('genres');
  if (!lineup) { bands.innerHTML = '<span class="muted">-</span>'; return; }
  const r = await (await fetch('/api/preview?lineup=' + encodeURIComponent(lineup))).json();
  if (!r.info || r.info.error) {
    bands.innerHTML = '<span class="muted">unreadable</span>'; return;
  }
  genres.innerHTML = r.info.genres.map(g => '<span class="chip">' + g + '</span>').join('')
    || '<span class="muted">no genre priority</span>';
  bands.innerHTML = '';
  r.info.bands.forEach(b => {
    const d = document.createElement('div'); d.textContent = b; bands.appendChild(d);
  });
}

async function loadTasks() {
  const r = await (await fetch('/api/tasks')).json();
  $('tasks').textContent = r.text.trim() || '-';
}

let es = null;
function startStream() {
  const log = $('log'); log.innerHTML = '';
  if (es) es.close();
  es = new EventSource('/api/stream');
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    const line = document.createElement('div');
    if (ev.type === 'error') line.className = 'err';
    else if (/tidal\.com|log in/i.test(ev.text)) line.className = 'login';
    line.appendChild(linkify(ev.text));
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
    if (ev.type === 'done') { finishRun(); }
  };
  es.onerror = () => { /* the browser reconnects; we close ourselves at run end */ };
}

function finishRun() {
  if (es) { es.close(); es = null; }
  $('run').disabled = false;
  $('status').textContent = 'Done.';
  loadTasks();
}

async function run() {
  $('run').disabled = true;
  $('status').textContent = 'Running ...';
  const r = await fetch('/api/run', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(currentConfig())});
  const j = await r.json();
  if (!r.ok) {
    $('status').textContent = 'Error: ' + (j.error || r.status);
    $('run').disabled = false;
    return;
  }
  startStream();
}

// --- wiring ---
$('lineup').addEventListener('change', () => { preview(); saveSoon(); });
['target', 'mode'].forEach(g => $(g).addEventListener('click', e => {
  if (e.target.dataset.v) {
    setSeg(g, e.target.dataset.v);
    $('plexbox').classList.toggle('show',
      [...$('target').children].find(b => b.classList.contains('on')).dataset.v === 'plex');
    saveSoon();
  }
}));
['top', 'name', 'dry', 'plex_baseurl', 'plex_token', 'plex_library'].forEach(id =>
  $(id).addEventListener('change', saveSoon));
$('run').addEventListener('click', run);

loadState();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# SERVER START
# ---------------------------------------------------------------------------

def serve(port=DEFAULT_PORT, open_browser=True):
    url = f"http://localhost:{port}/"
    try:
        httpd = ThreadingHTTPServer((HOST, port), Handler)
    except PermissionError:
        sys.exit(f"Port {port} needs elevated privileges (privileged port "
                 f"< 1024).\nEither start with 'sudo' or pick a higher port, "
                 f"e.g.:  python festival_cli.py -w 6660")
    except OSError as e:
        sys.exit(f"Cannot open port {port} ({e}).\n"
                 f"Different port:  python festival_cli.py -w 6660")

    print(f"Lineup2Playlist - web interface running at {url}")
    print("Stop with Ctrl-C.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else DEFAULT_PORT
    serve(p)
