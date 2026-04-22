#!/usr/bin/env python3
"""
EasyPlay dashboard — status view for a Pi running EasyPlay.

Two run modes:

  LOCAL MODE (on the Pi itself):
    python3 dashboard.py --local
    → Flask server on 0.0.0.0:8765, collects data via local subprocess calls.
    → Access from any device on the same Tailscale / LAN:
        http://<pi-ip>:8765

  REMOTE MODE (on the Mac, SSH into a Pi):
    python3 dashboard.py
    → Flask server on 127.0.0.1:8765, collects data via one SSH call per poll.
    → SSH ControlMaster keeps the connection warm (~130ms per query).

Design goal: minimum impact on the Pi's video playback.
  - No persistent dashboard process on the Pi (remote mode)
     OR one small Flask process with idle cost < 30 MB RAM, ~0% CPU (local mode)
  - Polling is manual (Refresh button) or hourly at most.
  - Each poll reads bounded, read-only data (ps, readlink, cat) — milliseconds.

Environment variables (remote mode):
  EASYPLAY_HOST   target Pi         default: luckypi2.local
  EASYPLAY_USER   ssh user          default: david
  EASYPLAY_PORT   dashboard port    default: 8765
  EASYPLAY_BIND   interface to bind default: 127.0.0.1 (remote) or 0.0.0.0 (local)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from flask import Flask, jsonify, render_template_string
except ImportError:
    sys.exit("Missing Flask. Run:  pip install flask")

HOST       = os.environ.get("EASYPLAY_HOST", "luckypi2.local")
USER       = os.environ.get("EASYPLAY_USER", "david")
DASH_PORT  = int(os.environ.get("EASYPLAY_PORT", "8765"))

SSH_CTRL_PATH = f"/tmp/easyplay-dashboard-{USER}-{HOST}.sock"
SSH_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", f"ControlPath={SSH_CTRL_PATH}",
    "-o", "ControlPersist=10m",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
]


# ── Status collector (runs on the Pi, either directly or via ssh) ───────────
#
# All queries are read-only and bounded (milliseconds of CPU). Called from
# Flask locally OR invoked remotely via `python3 dashboard.py --collect`.

def _sh(cmd: str, timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def collect_status() -> dict:
    """Collect the full status dict. Runs locally on the Pi."""
    data = {"ts": time.time()}

    # --- process state ------------------------------------------------------
    easyplay_pid = None
    for line in _sh(r"pgrep -af 'easyplay[0-9].*\.py'").splitlines():
        parts = line.split(maxsplit=1)
        if parts and parts[0].isdigit():
            easyplay_pid = int(parts[0])
            break

    watcher_pid = None
    for line in _sh("pgrep -af easyplay_watcher").splitlines():
        parts = line.split(maxsplit=1)
        if parts and parts[0].isdigit():
            watcher_pid = int(parts[0])
            break

    proc = {}
    if easyplay_pid:
        ps = _sh(f"ps -p {easyplay_pid} -o pid=,%cpu=,%mem=,etime=,cmd= 2>/dev/null")
        if ps:
            fields = ps.split(None, 4)
            proc = {
                "pid":    int(fields[0]),
                "cpu":    float(fields[1]),
                "mem":    float(fields[2]),
                "uptime": fields[3],
                "cmd":    fields[4] if len(fields) > 4 else "",
            }
    data["easyplay"] = proc

    watcher = {}
    if watcher_pid:
        ps = _sh(f"ps -p {watcher_pid} -o pid=,%cpu=,%mem=,etime= 2>/dev/null")
        if ps:
            fields = ps.split()
            watcher = {
                "pid":    int(fields[0]),
                "cpu":    float(fields[1]),
                "mem":    float(fields[2]),
                "uptime": fields[3],
            }
    data["watcher"] = watcher

    # --- currently playing --------------------------------------------------
    playing = None
    if easyplay_pid:
        try:
            fd_dir = Path(f"/proc/{easyplay_pid}/fd")
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(str(fd))
                except OSError:
                    continue
                low = target.lower()
                if any(low.endswith(ext) for ext in (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v")):
                    playing = target
                    break
        except OSError:
            pass
    data["playing_file"] = playing

    # --- progress -----------------------------------------------------------
    progress_path = Path.home() / "Desktop" / "EasyPlay" / "easyplay_progress.json"
    history = []
    current_progress = None
    try:
        prog = json.loads(progress_path.read_text())
        entries = []
        for key, v in prog.items():
            entries.append({
                "path":         v.get("path", key),
                "name":         v.get("name", key.split("/")[-1]),
                "position_sec": v.get("position_sec", 0),
                "duration_sec": v.get("duration_sec", 0),
                "completed":    bool(v.get("completed")),
                "last_updated": v.get("last_updated", ""),
            })
        entries.sort(key=lambda e: e["last_updated"], reverse=True)
        history = entries
        if playing:
            for e in entries:
                if e["path"] == playing:
                    current_progress = e
                    break
    except Exception:
        pass
    data["history"]          = history
    data["current_progress"] = current_progress

    # --- BLE / watcher ------------------------------------------------------
    ble = {
        "watcher_service": _sh("systemctl is-active easyplay-watcher.service") or "unknown",
        "recent_log":     [],
        "adapter":        _sh("hciconfig hci0 2>/dev/null | head -3"),
    }
    log = _sh("journalctl -u easyplay-watcher.service --no-pager -n 5 2>/dev/null")
    if log:
        ble["recent_log"] = log.split("\n")
    data["ble"] = ble

    # --- system -------------------------------------------------------------
    sysinfo = {
        "hostname":         _sh("hostname"),
        "uptime":           _sh("uptime -p"),
        "load":             _sh("uptime | sed 's/.*load average: //'"),
        "mem":              _sh("free -h | awk '/Mem:/ {print $3\" / \"$2\" used\"}'"),
        "disk_sd":          _sh("df -h / | tail -1 | awk '{print $3\" / \"$2\" (\"$5\" used)\"}'"),
        "disk_media":       _sh("df -h /mnt/media 2>/dev/null | tail -1 | awk '{print $3\" / \"$2\" (\"$5\" used)\"}'"),
        "temp_c":           "",
        "ip":               _sh("ip -brief addr | grep -E 'UP' | awk '{print $1\": \"$3}' | head -3 | tr '\\n' ' '"),
        "service_easyplay": _sh("systemctl is-active easyplay.service 2>/dev/null") or "",
    }
    temp_raw = _sh("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null")
    if temp_raw.isdigit():
        sysinfo["temp_c"] = f"{int(temp_raw)/1000:.1f}°C"
    data["system"] = sysinfo

    return data


# ── Remote-mode fetcher: runs `dashboard.py --collect` over SSH ─────────────

def fetch_remote() -> dict:
    start = time.monotonic()
    try:
        # The Pi-side script is this same file; we expect it at the path below.
        remote_script = "/home/david/Desktop/EasyPlay/tools/dashboard/dashboard.py"
        result = subprocess.run(
            ["ssh", *SSH_OPTS, f"{USER}@{HOST}",
             f"python3 {remote_script} --collect"],
            capture_output=True, text=True, timeout=15,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if result.returncode != 0:
            return {"error": f"ssh exit {result.returncode}: {(result.stderr or '').strip()[:300]}",
                    "elapsed_ms": elapsed_ms}
        out = (result.stdout or "").strip()
        first = out.find("{")
        last  = out.rfind("}")
        if first == -1 or last == -1:
            return {"error": f"no JSON in output: {out[:300]!r}", "elapsed_ms": elapsed_ms}
        data = json.loads(out[first:last+1])
        data["_fetch_elapsed_ms"] = elapsed_ms
        return data
    except subprocess.TimeoutExpired:
        return {"error": "ssh timeout (>15s)",
                "elapsed_ms": int((time.monotonic() - start) * 1000)}
    except Exception as e:
        return {"error": f"fetch failed: {e}",
                "elapsed_ms": int((time.monotonic() - start) * 1000)}


# ── Dashboard HTML (inline so this file is self-contained) ──────────────────

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>EasyPlay Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
      background: #0f0f14; color: #e5e5ea; padding: 2rem;
      max-width: 960px; margin: 0 auto;
    }
    h1 {
      font-size: 1.4rem; font-weight: 600; margin-bottom: 0.2rem;
      color: #c0a5ff;
    }
    .subtitle { color: #7a7a80; font-size: 0.85rem; margin-bottom: 1.5rem; }

    .controls { display: flex; gap: 0.75rem; align-items: center;
                margin-bottom: 1.5rem; flex-wrap: wrap; }
    button {
      background: #2d2542; color: #d8c8ff; border: 1px solid #433268;
      padding: 0.5rem 1.1rem; border-radius: 6px; font-size: 0.9rem;
      cursor: pointer; transition: background 0.15s;
    }
    button:hover { background: #3a2f54; }
    button:disabled { opacity: 0.5; cursor: wait; }
    .stamp { color: #7a7a80; font-size: 0.8rem; }

    .card {
      background: #17171f; border: 1px solid #23232d;
      border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 1rem;
    }
    .card h2 {
      font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em;
      color: #a893e8; margin-bottom: 0.6rem; font-weight: 600;
    }
    .kv { display: grid; grid-template-columns: 140px 1fr;
          gap: 0.3rem 1rem; font-size: 0.92rem; }
    .kv .k { color: #7a7a80; }
    .kv .v { color: #e5e5ea; font-variant-numeric: tabular-nums; word-break: break-word; }

    .badge {
      display: inline-block; padding: 0.15rem 0.55rem; border-radius: 4px;
      font-size: 0.75rem; font-weight: 600;
    }
    .badge.ok   { background: #143b20; color: #6fd98c; }
    .badge.warn { background: #3b2d14; color: #e0b66c; }
    .badge.bad  { background: #3b1420; color: #e06c6c; }
    .badge.dim  { background: #242430; color: #7a7a80; }

    .progress {
      background: #1f1f28; height: 10px; border-radius: 5px;
      overflow: hidden; margin-top: 0.35rem;
    }
    .progress > div {
      height: 100%; background: linear-gradient(90deg, #5b3ea8, #7c5cd4);
      transition: width 0.4s;
    }

    .title-big { font-size: 1.1rem; font-weight: 600; color: #e5e5ea;
                 margin-bottom: 0.3rem; }

    .history-row {
      display: grid; grid-template-columns: 60px 1fr 80px;
      gap: 0.8rem; padding: 0.45rem 0;
      border-bottom: 1px solid #1c1c24; font-size: 0.88rem;
      align-items: center;
    }
    .history-row:last-child { border-bottom: none; }
    .history-row .pct { color: #a893e8; font-variant-numeric: tabular-nums; text-align: right; }
    .history-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .history-row.done .pct { color: #6fd98c; }
    .history-row .age { color: #5a5a65; font-size: 0.8rem; }

    details { margin-top: 0.5rem; }
    summary { cursor: pointer; color: #a893e8; font-size: 0.88rem;
              padding: 0.4rem 0; user-select: none; }
    summary:hover { color: #c5b0ff; }

    .error-card { background: #2a1218; border-color: #5a2230; }
    .error-card h2 { color: #e06c6c; }
    .error-card pre { white-space: pre-wrap; color: #e5e5ea; font-size: 0.85rem; }

    .log-line { font-family: monospace; font-size: 0.78rem;
                color: #9090a0; padding: 0.1rem 0; word-break: break-all; }
  </style>
</head>
<body>
  <h1>EasyPlay Dashboard</h1>
  <p class="subtitle"><span id="target"></span></p>

  <div class="controls">
    <button id="refresh-btn" onclick="refresh()">Refresh</button>
    <label style="color:#7a7a80; font-size: 0.85rem;">
      <input type="checkbox" id="autorefresh" /> Auto-refresh hourly
    </label>
    <span class="stamp" id="stamp">never</span>
  </div>

  <div id="content">
    <div class="card"><p style="color:#7a7a80;">Click Refresh to query the Pi.</p></div>
  </div>

<script>
const content = document.getElementById('content');
const stamp   = document.getElementById('stamp');
const btn     = document.getElementById('refresh-btn');
const target  = document.getElementById('target');

let autoTimer = null;
const autoCheckbox = document.getElementById('autorefresh');
autoCheckbox.addEventListener('change', () => {
  if (autoCheckbox.checked) {
    autoTimer = setInterval(refresh, 3600 * 1000);
  } else if (autoTimer) {
    clearInterval(autoTimer); autoTimer = null;
  }
});

function fmtTime(sec) {
  sec = Math.floor(sec || 0);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m ${s}s`;
}
function pct(cur, total) { if (!total) return 0; return Math.round((cur / total) * 100); }
function fmtAge(isoStr) {
  if (!isoStr) return '';
  const t = Date.parse(isoStr);
  if (isNaN(t)) return '';
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec/60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec/3600)}h ago`;
  return `${Math.floor(sec/86400)}d ago`;
}

async function refresh() {
  btn.disabled = true;
  btn.textContent = 'Querying...';
  try {
    const r = await fetch('/api/status');
    const data = await r.json();
    render(data);
    stamp.textContent = 'Last: ' + new Date().toLocaleTimeString()
                      + (data._fetch_elapsed_ms ? ` (${data._fetch_elapsed_ms}ms)` : '');
  } catch (e) {
    content.innerHTML = `<div class="card error-card"><h2>Error</h2><pre>${e}</pre></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Refresh';
  }
}

function render(d) {
  if (d.error) {
    content.innerHTML = `<div class="card error-card">
      <h2>Connection error</h2>
      <pre>${d.error}</pre>
    </div>`;
    return;
  }

  target.textContent = `${d.system?.hostname || '(unknown host)'} · ${d.system?.ip || ''}`;

  const parts = [];

  // Now playing
  if (d.current_progress) {
    const e = d.current_progress;
    const p = pct(e.position_sec, e.duration_sec);
    parts.push(`<div class="card">
      <h2>▶ Now Playing</h2>
      <div class="title-big">${escapeHtml(e.name || e.path.split('/').pop())}</div>
      <div class="kv">
        <span class="k">Position</span><span class="v">${fmtTime(e.position_sec)} / ${fmtTime(e.duration_sec)} · ${p}%</span>
      </div>
      <div class="progress"><div style="width: ${p}%"></div></div>
    </div>`);
  } else if (d.playing_file) {
    parts.push(`<div class="card">
      <h2>▶ Now Playing (no progress data)</h2>
      <div class="v">${escapeHtml(d.playing_file.split('/').pop())}</div>
    </div>`);
  } else {
    parts.push(`<div class="card">
      <h2>▶ Now Playing</h2>
      <span class="badge dim">Idle — nothing playing</span>
    </div>`);
  }

  const ep = d.easyplay || {};
  const wp = d.watcher || {};
  const epBadge = ep.pid
    ? `<span class="badge ok">running · PID ${ep.pid}</span>`
    : `<span class="badge bad">not running</span>`;
  const wpBadge = wp.pid
    ? `<span class="badge ok">running · PID ${wp.pid}</span>`
    : `<span class="badge warn">not running</span>`;

  parts.push(`<div class="card">
    <h2>⚙ Process State</h2>
    <div class="kv">
      <span class="k">easyplay</span><span class="v">${epBadge}</span>
      ${ep.pid ? `
      <span class="k">uptime</span><span class="v">${ep.uptime || '?'}</span>
      <span class="k">CPU</span><span class="v">${ep.cpu?.toFixed(1) ?? '?'} %</span>
      <span class="k">memory</span><span class="v">${ep.mem?.toFixed(1) ?? '?'} %</span>
      ` : ''}
      <span class="k">watcher</span><span class="v">${wpBadge}</span>
      ${wp.pid ? `<span class="k">watcher uptime</span><span class="v">${wp.uptime || '?'}</span>` : ''}
      <span class="k">service</span><span class="v"><span class="badge ${d.system?.service_easyplay === 'active' ? 'ok' : 'warn'}">${d.system?.service_easyplay || 'unknown'}</span></span>
    </div>
  </div>`);

  const ble = d.ble || {};
  const wStatus = ble.watcher_service || 'unknown';
  parts.push(`<div class="card">
    <h2>📡 BLE Remote</h2>
    <div class="kv">
      <span class="k">watcher service</span><span class="v">
        <span class="badge ${wStatus === 'active' ? 'ok' : 'warn'}">${wStatus}</span>
      </span>
      <span class="k">adapter</span><span class="v"><pre style="font-size:0.78rem; color:#9090a0;">${escapeHtml(ble.adapter || '(none)')}</pre></span>
    </div>
    <details><summary>Recent watcher log</summary>
      ${(ble.recent_log || []).map(l => `<div class="log-line">${escapeHtml(l)}</div>`).join('')}
    </details>
  </div>`);

  const s = d.system || {};
  parts.push(`<div class="card">
    <h2>🖥 System</h2>
    <div class="kv">
      <span class="k">hostname</span><span class="v">${escapeHtml(s.hostname || '?')}</span>
      <span class="k">uptime</span><span class="v">${escapeHtml(s.uptime || '?')}</span>
      <span class="k">load avg</span><span class="v">${escapeHtml(s.load || '?')}</span>
      <span class="k">CPU temp</span><span class="v">${escapeHtml(s.temp_c || '?')}</span>
      <span class="k">memory</span><span class="v">${escapeHtml(s.mem || '?')}</span>
      <span class="k">disk /</span><span class="v">${escapeHtml(s.disk_sd || '?')}</span>
      <span class="k">disk /mnt/media</span><span class="v">${escapeHtml(s.disk_media || 'not mounted')}</span>
      <span class="k">network</span><span class="v">${escapeHtml(s.ip || '?')}</span>
    </div>
  </div>`);

  const h = d.history || [];
  const watched = h.filter(e => e.position_sec > 60 || e.completed);
  parts.push(`<div class="card">
    <h2>📚 Watched History (${watched.length})</h2>
    <details open>
      <summary>Show titles</summary>
      <div style="margin-top: 0.5rem;">
        ${watched.slice(0, 30).map(e => {
          const p = pct(e.position_sec, e.duration_sec);
          const done = e.completed || p >= 95;
          return `<div class="history-row ${done ? 'done' : ''}">
            <span class="age">${fmtAge(e.last_updated)}</span>
            <span class="name">${escapeHtml(e.name || e.path.split('/').pop())}</span>
            <span class="pct">${done ? '✓ done' : p + '%'}</span>
          </div>`;
        }).join('')}
        ${watched.length === 0 ? '<div style="color:#7a7a80; font-size:0.85rem;">No titles watched yet.</div>' : ''}
      </div>
    </details>
  </div>`);

  content.innerHTML = parts.join('');
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[<>&"']/g, c => ({
    '<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'
  })[c]);
}

refresh();
</script>
</body>
</html>
"""


# ── Flask app ───────────────────────────────────────────────────────────────

def make_app(local: bool) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/status")
    def api_status():
        t0 = time.monotonic()
        if local:
            data = collect_status()
            data["_fetch_elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            return jsonify(data)
        return jsonify(fetch_remote())

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true",
                    help="Run on the Pi — collect data via local subprocess (not SSH)")
    ap.add_argument("--collect", action="store_true",
                    help="Print status JSON to stdout and exit (invoked over SSH in remote mode)")
    args = ap.parse_args()

    if args.collect:
        print(json.dumps(collect_status()))
        return

    bind = os.environ.get("EASYPLAY_BIND")
    if bind is None:
        bind = "0.0.0.0" if args.local else "127.0.0.1"

    if args.local:
        print(f"\nEasyPlay dashboard (LOCAL): http://{bind}:{DASH_PORT}\n")
    else:
        print(f"\nEasyPlay dashboard (REMOTE via ssh): http://{bind}:{DASH_PORT}  →  {USER}@{HOST}\n")

    app = make_app(local=args.local)
    app.run(host=bind, port=DASH_PORT, debug=False)


if __name__ == "__main__":
    main()
