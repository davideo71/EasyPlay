#!/usr/bin/env python3
"""
EasyPlay dashboard — Mac-side status view for LuckyPi2.

Starts a small Flask server on the Mac (localhost:8765 by default) that
serves a web dashboard showing the state of easyplay on the Pi.

Design principle: ZERO IMPACT on the Pi's video playback.

- No daemon / persistent service runs on the Pi.
- Polling is MANUAL (Refresh button) or hourly at most.
- Single SSH call per poll, using ControlMaster so repeated calls
  piggy-back on one persistent channel (~10ms per query).
- All queries are read-only and bounded in time (milliseconds of Pi CPU).

Usage:
    python3 tools/dashboard/dashboard.py
    # then open http://localhost:8765 in a browser

Environment variables:
    EASYPLAY_HOST   target Pi (default: luckypi2.local)
    EASYPLAY_USER   ssh user (default: david)
    EASYPLAY_PORT   dashboard local port (default: 8765)
"""

from __future__ import annotations

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

# SSH ControlMaster keeps one connection warm; subsequent queries reuse it.
# Socket lives in /tmp; persists 10 minutes after last use.
SSH_CTRL_PATH = f"/tmp/easyplay-dashboard-{USER}-{HOST}.sock"
SSH_OPTS = [
    "-o", f"ControlMaster=auto",
    "-o", f"ControlPath={SSH_CTRL_PATH}",
    "-o", "ControlPersist=10m",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
]

# ── The single SSH command that gathers everything in one round-trip ────────
#
# Writes a JSON blob to stdout. All the queries are bounded, read-only, and
# take milliseconds. No sudo (only systemctl is-active/list-units which are
# fine for a regular user).
REMOTE_SCRIPT = r'''
python3 - <<'PYEOF'
import json, os, re, time
from pathlib import Path

def sh(cmd):
    import subprocess
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
        return (r.stdout or "").strip()
    except Exception as e:
        return ""

data = {"ts": time.time()}

# --- process ----------------------------------------------------------------
easyplay_pid = None
for line in sh("pgrep -af 'easyplay[0-9].*\\.py'").splitlines():
    parts = line.split(maxsplit=1)
    if parts and parts[0].isdigit():
        easyplay_pid = int(parts[0]); break

watcher_pid = None
for line in sh("pgrep -af easyplay_watcher").splitlines():
    parts = line.split(maxsplit=1)
    if parts and parts[0].isdigit():
        watcher_pid = int(parts[0]); break

proc = {}
if easyplay_pid:
    ps = sh(f"ps -p {easyplay_pid} -o pid=,%cpu=,%mem=,etime=,cmd= 2>/dev/null")
    if ps:
        fields = ps.split(None, 4)
        proc = {
            "pid":     int(fields[0]),
            "cpu":     float(fields[1]),
            "mem":     float(fields[2]),
            "uptime":  fields[3],
            "cmd":     fields[4] if len(fields) > 4 else "",
        }
data["easyplay"] = proc

watcher = {}
if watcher_pid:
    ps = sh(f"ps -p {watcher_pid} -o pid=,%cpu=,%mem=,etime= 2>/dev/null")
    if ps:
        fields = ps.split()
        watcher = {"pid": int(fields[0]), "cpu": float(fields[1]),
                   "mem": float(fields[2]), "uptime": fields[3]}
data["watcher"] = watcher

# --- currently playing ------------------------------------------------------
playing = None
if easyplay_pid:
    # readlink /proc/PID/fd/* — find any open video file.
    # This does NOT require sudo for the user's own process.
    try:
        fd_dir = Path(f"/proc/{easyplay_pid}/fd")
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(str(fd))
                low = target.lower()
                if any(low.endswith(ext) for ext in (".mp4",".mkv",".avi",".mov",".webm",".m4v")):
                    playing = target
                    break
            except OSError:
                continue
    except OSError:
        pass
data["playing_file"] = playing

# --- progress ---------------------------------------------------------------
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
                current_progress = e; break
except Exception:
    pass
data["history"]          = history
data["current_progress"] = current_progress

# --- BLE --------------------------------------------------------------------
ble = {}
try:
    wstatus = sh("systemctl is-active easyplay-watcher.service")
    ble["watcher_service"] = wstatus or "unknown"
    # Last 20 log lines from watcher
    ble_log = sh("journalctl -u easyplay-watcher.service --no-pager -n 10 2>/dev/null | tail -5")
    ble["recent_log"] = ble_log.split("\n") if ble_log else []
    # BT adapter state
    hci = sh("hciconfig hci0 2>/dev/null | head -3")
    ble["adapter"] = hci
except Exception:
    pass
data["ble"] = ble

# --- system -----------------------------------------------------------------
sysinfo = {
    "hostname":  sh("hostname"),
    "uptime":    sh("uptime -p"),
    "load":      sh("uptime | sed 's/.*load average: //'"),
    "mem":       sh("free -h | awk '/Mem:/ {print $3\" / \"$2\" used\"}'"),
    "disk_sd":   sh("df -h / | tail -1 | awk '{print $3\" / \"$2\" (\"$5\" used)\"}'"),
    "disk_media": sh("df -h /mnt/media 2>/dev/null | tail -1 | awk '{print $3\" / \"$2\" (\"$5\" used)\"}'"),
    "temp_c":    "",
    "ip":        sh("ip -brief addr | grep -E 'UP' | awk '{print $1\": \"$3}' | head -3 | tr '\\n' ' '"),
    "service_easyplay": sh("systemctl is-active easyplay.service 2>/dev/null") or "",
}
try:
    temp_raw = sh("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null")
    if temp_raw.isdigit():
        sysinfo["temp_c"] = f"{int(temp_raw)/1000:.1f}°C"
except Exception:
    pass
data["system"] = sysinfo

print(json.dumps(data))
PYEOF
'''


def fetch_status() -> dict:
    """One round-trip SSH call that runs REMOTE_SCRIPT and returns parsed JSON.

    Returns {"error": "..."} on failure. First call pays SSH handshake; later
    calls reuse the ControlMaster channel for near-zero latency.
    """
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["ssh", *SSH_OPTS, f"{USER}@{HOST}", "bash", "-c", REMOTE_SCRIPT],
            capture_output=True, text=True, timeout=15,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if result.returncode != 0:
            return {"error": f"ssh exit {result.returncode}: {(result.stderr or '').strip()[:300]}",
                    "elapsed_ms": elapsed_ms}
        out = (result.stdout or "").strip()
        # The script prints only the JSON — any pre/post output is from shell
        first = out.find("{")
        last  = out.rfind("}")
        if first == -1 or last == -1:
            return {"error": f"no JSON in output (first 300 chars: {out[:300]!r})",
                    "elapsed_ms": elapsed_ms}
        data = json.loads(out[first:last+1])
        data["_fetch_elapsed_ms"] = elapsed_ms
        return data
    except subprocess.TimeoutExpired:
        return {"error": "ssh timeout (>15s)", "elapsed_ms": int((time.monotonic() - start) * 1000)}
    except Exception as e:
        return {"error": f"fetch failed: {e}", "elapsed_ms": int((time.monotonic() - start) * 1000)}


# ── Dashboard HTML (inline so this file is self-contained) ──────────────────

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
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
      display: grid; grid-template-columns: 52px 1fr 80px;
      gap: 0.8rem; padding: 0.45rem 0;
      border-bottom: 1px solid #1c1c24; font-size: 0.88rem;
      align-items: center;
    }
    .history-row:last-child { border-bottom: none; }
    .history-row .pct { color: #a893e8; font-variant-numeric: tabular-nums; text-align: right; }
    .history-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .history-row.done .pct { color: #6fd98c; }

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
    // 1 hour = 3600000 ms
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
function pct(cur, total) {
  if (!total) return 0;
  return Math.round((cur / total) * 100);
}
function fmtAge(isoStr) {
  if (!isoStr) return '';
  const now = Date.now();
  let t = Date.parse(isoStr);
  if (isNaN(t)) return '';
  const sec = Math.floor((now - t) / 1000);
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
      <p style="color:#7a7a80; margin-top:0.5rem; font-size:0.8rem;">
        Check that the Pi is on and reachable. SSH key should be authorized.
      </p>
    </div>`;
    return;
  }

  target.textContent = `${d.system?.hostname || '(unknown host)'} · ${d.system?.ip || ''}`;

  const parts = [];

  // --- Now playing ---
  if (d.current_progress) {
    const e = d.current_progress;
    const p = pct(e.position_sec, e.duration_sec);
    parts.push(`<div class="card">
      <h2>▶ Now Playing</h2>
      <div class="title-big">${e.name || e.path.split('/').pop()}</div>
      <div class="kv">
        <span class="k">Position</span><span class="v">${fmtTime(e.position_sec)} / ${fmtTime(e.duration_sec)} · ${p}%</span>
      </div>
      <div class="progress"><div style="width: ${p}%"></div></div>
    </div>`);
  } else if (d.playing_file) {
    parts.push(`<div class="card">
      <h2>▶ Now Playing (no progress data)</h2>
      <div class="v">${d.playing_file.split('/').pop()}</div>
    </div>`);
  } else {
    parts.push(`<div class="card">
      <h2>▶ Now Playing</h2>
      <span class="badge dim">Idle — nothing playing</span>
    </div>`);
  }

  // --- Process state ---
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

  // --- BLE ---
  const ble = d.ble || {};
  const wStatus = ble.watcher_service || 'unknown';
  parts.push(`<div class="card">
    <h2>📡 BLE Remote</h2>
    <div class="kv">
      <span class="k">watcher service</span><span class="v">
        <span class="badge ${wStatus === 'active' ? 'ok' : 'warn'}">${wStatus}</span>
      </span>
      <span class="k">adapter</span><span class="v"><pre style="font-size:0.78rem; color:#9090a0;">${ble.adapter || '(none)'}</pre></span>
    </div>
    <details><summary>Recent watcher log</summary>
      ${(ble.recent_log || []).map(l => `<div class="log-line">${escapeHtml(l)}</div>`).join('')}
    </details>
  </div>`);

  // --- System ---
  const s = d.system || {};
  parts.push(`<div class="card">
    <h2>🖥 System</h2>
    <div class="kv">
      <span class="k">hostname</span><span class="v">${s.hostname || '?'}</span>
      <span class="k">uptime</span><span class="v">${s.uptime || '?'}</span>
      <span class="k">load avg</span><span class="v">${s.load || '?'}</span>
      <span class="k">CPU temp</span><span class="v">${s.temp_c || '?'}</span>
      <span class="k">memory</span><span class="v">${s.mem || '?'}</span>
      <span class="k">disk /</span><span class="v">${s.disk_sd || '?'}</span>
      <span class="k">disk /mnt/media</span><span class="v">${s.disk_media || 'not mounted'}</span>
      <span class="k">network</span><span class="v">${s.ip || '?'}</span>
    </div>
  </div>`);

  // --- History ---
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
            <span class="age" style="color:#5a5a65; font-size:0.8rem;">${fmtAge(e.last_updated)}</span>
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

// Initial load.
refresh();
</script>
</body>
</html>
"""

# ── Flask app ───────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/status")
def api_status():
    return jsonify(fetch_status())


if __name__ == "__main__":
    print(f"\nEasyPlay dashboard: http://localhost:{DASH_PORT}  →  {USER}@{HOST}\n")
    app.run(host="127.0.0.1", port=DASH_PORT, debug=False)
