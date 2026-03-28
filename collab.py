#!/usr/bin/env python3
"""
Claude Code Collaboration Harness
==================================
Real-time collaboration between multiple Claude Code instances.
Zero external dependencies — Python 3.12+ stdlib only.

Architecture:
  - Pure file-based state (JSON) in a shared `state/` directory
  - OS-level file locking for concurrent access safety
  - Each Claude Code instance is a "node" identified by a unique name
  - Nodes communicate via messages, share context, coordinate tasks, and lock files
  - The `poll` command gives each node a real-time feed of changes

Usage:
    python collab.py <command> [args...]
    python collab.py --help
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.3.0"

# ── Configuration ─────────────────────────────────────────────

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent

DEFAULT_STATE_DIR = Path(os.environ.get(
    "COLLAB_STATE_DIR",
    str(SCRIPT_DIR / "state")
))
LOCK_TIMEOUT = 5          # seconds to wait for OS file lock
STALE_LOCK_SEC = 10       # seconds before a lock file is considered stale
LOG_MAX = 1000            # max activity log entries
MSG_MAX = 500             # max messages to retain

# Per-role identity — ANSI colors and Windows Terminal tab colors
ROLE_STYLES = {
    "lead": {"ansi": "\033[1;33m", "label": "LEAD", "tab_hex": "#E5A00D",
             "desc": "Coordination & Architecture"},
    "dev1": {"ansi": "\033[1;36m", "label": "DEV 1", "tab_hex": "#00B4D8",
             "desc": "Primary Implementation"},
    "dev2": {"ansi": "\033[1;32m", "label": "DEV 2", "tab_hex": "#2DC653",
             "desc": "Review, Testing & Secondary Dev"},
}
ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[2m"


# ── Utilities ─────────────────────────────────────────────────

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)

def ago(iso: str) -> str:
    try:
        s = int((datetime.now(timezone.utc) - parse_ts(iso)).total_seconds())
        if s < 0: return "just now"
        if s < 60: return f"{s}s ago"
        if s < 3600: return f"{s // 60}m ago"
        if s < 86400: return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"
    except Exception:
        return "?"

def short_time(iso: str) -> str:
    try:
        return parse_ts(iso).strftime("%H:%M:%S")
    except Exception:
        return "??:??:??"

def trunc(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[:n - 3] + "..."


# ── Signal Files (push-style notification) ───────────���────────

def _signal_path(state_dir: Path, node: str) -> Path:
    return state_dir / f"_signal_{node}"

def signal_node(state_dir: Path, node: str, reason: str):
    """Touch a signal file for a node so it knows to poll.
    The file contains the reason, appended line by line."""
    p = _signal_path(state_dir, node)
    try:
        with open(str(p), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {reason}\n")
    except OSError:
        pass

def read_and_clear_signal(state_dir: Path, node: str) -> list:
    """Read all pending signal lines and clear the file. Returns list of strings."""
    p = _signal_path(state_dir, node)
    lines = []
    try:
        if p.exists():
            lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            p.unlink()
    except OSError:
        pass
    return lines


# ── Window Control (Win32 API via ctypes) ────────────────────

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes
    import struct

    kernel32 = ctypes.windll.kernel32

    # Console input record constants
    KEY_EVENT = 0x0001
    VK_RETURN = 0x0D
    VK_ESCAPE = 0x1B

    # Console input record structure
    class KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [
            ("bKeyDown", ctypes.wintypes.BOOL),
            ("wRepeatCount", ctypes.wintypes.WORD),
            ("wVirtualKeyCode", ctypes.wintypes.WORD),
            ("wVirtualScanCode", ctypes.wintypes.WORD),
            ("uChar", ctypes.wintypes.WCHAR),
            ("dwControlKeyState", ctypes.wintypes.DWORD),
        ]

    class INPUT_RECORD_Event(ctypes.Union):
        _fields_ = [("KeyEvent", KEY_EVENT_RECORD)]

    class INPUT_RECORD(ctypes.Structure):
        _fields_ = [
            ("EventType", ctypes.wintypes.WORD),
            ("_padding", ctypes.wintypes.WORD),
            ("Event", INPUT_RECORD_Event),
        ]

    ATTACH_PARENT_PROCESS = -1
    STD_INPUT_HANDLE = -10

    def _get_role_cmd_pids() -> dict:
        """Map role names to their cmd.exe PIDs via WMIC."""
        try:
            result = subprocess.run(
                ["wmic", "process", "where", "name='cmd.exe'",
                 "get", "processid,commandline", "/format:csv"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            return {}
        role_pids = {}
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("Node"):
                continue
            # CSV format: Hostname,CommandLine,ProcessId
            # PID is always the last field (pure digits)
            # CommandLine may contain commas, so grab last field as PID
            # and everything between first comma and last comma as cmdline
            first_comma = line.find(",")
            last_comma = line.rfind(",")
            if first_comma == -1 or first_comma == last_comma:
                continue
            cmdline = line[first_comma + 1 : last_comma]
            pid_str = line[last_comma + 1 :].strip()
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            if "_run_lead.bat" in cmdline:
                role_pids["lead"] = pid
            elif "_run_dev1.bat" in cmdline:
                role_pids["dev1"] = pid
            elif "_run_dev2.bat" in cmdline:
                role_pids["dev2"] = pid
        return role_pids

    # ── Helper script for console injection ──
    # Spawned as a subprocess so we don't disturb our own console.
    # Uses CONIN$ (not GetStdHandle) to get the real console input buffer.
    _INJECTOR_SCRIPT = r'''
import ctypes, ctypes.wintypes, sys, time

KEY_EVENT = 0x0001
GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = -1

kernel32 = ctypes.windll.kernel32

class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", ctypes.wintypes.BOOL),
        ("wRepeatCount", ctypes.wintypes.WORD),
        ("wVirtualKeyCode", ctypes.wintypes.WORD),
        ("wVirtualScanCode", ctypes.wintypes.WORD),
        ("uChar", ctypes.wintypes.WCHAR),
        ("dwControlKeyState", ctypes.wintypes.DWORD),
    ]

class INPUT_RECORD_Event(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD)]

class INPUT_RECORD(ctypes.Structure):
    _fields_ = [
        ("EventType", ctypes.wintypes.WORD),
        ("_padding", ctypes.wintypes.WORD),
        ("Event", INPUT_RECORD_Event),
    ]

def write_key(handle, char, vk=0):
    """Write a key-down + key-up pair to a console input buffer."""
    written = ctypes.wintypes.DWORD()
    for down in (True, False):
        rec = INPUT_RECORD()
        rec.EventType = KEY_EVENT
        rec.Event.KeyEvent.bKeyDown = down
        rec.Event.KeyEvent.wRepeatCount = 1
        rec.Event.KeyEvent.wVirtualKeyCode = vk
        rec.Event.KeyEvent.wVirtualScanCode = 0
        rec.Event.KeyEvent.uChar = char
        rec.Event.KeyEvent.dwControlKeyState = 0
        ok = kernel32.WriteConsoleInputW(handle, ctypes.byref(rec), 1, ctypes.byref(written))
        if not ok:
            err = ctypes.get_last_error()
            print(f"WriteConsoleInputW failed: error={err}", file=sys.stderr)
            return False
    return True

def open_conin():
    """Open CONIN$ to get a direct handle to the console input buffer."""
    handle = kernel32.CreateFileW(
        "CONIN$",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        print(f"CreateFileW(CONIN$) failed: error={err}", file=sys.stderr)
        return None
    return handle

def main():
    target_pid = int(sys.argv[1])
    action = sys.argv[2]          # "text", "escape", or "enter"
    payload = sys.argv[3] if len(sys.argv) > 3 else ""

    # Detach from our parent's console
    kernel32.FreeConsole()

    # Attach to the target's console
    if not kernel32.AttachConsole(target_pid):
        err = ctypes.get_last_error()
        print(f"AttachConsole failed for PID {target_pid} (error {err})", file=sys.stderr)
        sys.exit(1)

    # Open the console input buffer directly via CONIN$
    handle = open_conin()
    if handle is None:
        kernel32.FreeConsole()
        sys.exit(1)

    if action == "escape":
        write_key(handle, '\x1b', 0x1B)
        time.sleep(0.05)
        write_key(handle, '\x1b', 0x1B)  # Double-tap

    elif action == "text":
        for ch in payload:
            if ch == '\n':
                write_key(handle, '\r', 0x0D)
            else:
                write_key(handle, ch, 0)
            time.sleep(0.003)
        # Press Enter
        time.sleep(0.02)
        write_key(handle, '\r', 0x0D)

    elif action == "enter":
        write_key(handle, '\r', 0x0D)

    kernel32.CloseHandle(handle)
    kernel32.FreeConsole()
    print("OK")

if __name__ == "__main__":
    main()
'''

    def _run_injector(target_pid: int, action: str, payload: str = "") -> bool:
        """Spawn the injector helper to write to another console."""
        try:
            result = subprocess.run(
                [sys.executable, "-c", _INJECTOR_SCRIPT,
                 str(target_pid), action, payload],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    print(f"  [injector error] {stderr}")
                return False
            return "OK" in result.stdout
        except Exception as e:
            print(f"  [injector error] {e}")
            return False

    def find_collab_window(node_name: str) -> int:
        """Find the cmd.exe PID for a collaboration node. Returns PID or 0."""
        pids = _get_role_cmd_pids()
        return pids.get(node_name, 0)

else:
    # Non-Windows stubs
    def _get_role_cmd_pids():
        return {}
    def _run_injector(target_pid, action, payload=""):
        print("[ERROR] Window control only supported on Windows")
        return False
    def find_collab_window(node_name):
        return 0


# ── File Locking ──────────────────────────────────────────────

class FileLock:
    """Cross-process file lock via atomic exclusive-create."""

    def __init__(self, target: Path):
        self.lockpath = target.parent / (target.name + ".lock")

    def __enter__(self):
        deadline = time.time() + LOCK_TIMEOUT
        while time.time() < deadline:
            try:
                fd = os.open(str(self.lockpath), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except (FileExistsError, OSError):
                try:
                    if time.time() - os.path.getmtime(str(self.lockpath)) > STALE_LOCK_SEC:
                        os.unlink(str(self.lockpath))
                        continue
                except OSError:
                    pass
                time.sleep(0.02)
        raise TimeoutError(f"Lock timeout: {self.lockpath}")

    def __exit__(self, *_):
        try:
            os.unlink(str(self.lockpath))
        except OSError:
            pass


# ── State Manager ─────────────────────────────────────────────

_DEFAULTS = {
    "nodes": {},
    "messages": [],
    "context": {},
    "tasks": {},
    "locks": {},
    "log": [],
    "meta": {"next_task_id": 1},
}

class State:
    """JSON-file state store with OS-level locking for safe concurrent access."""

    def __init__(self, state_dir: Path):
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, default in _DEFAULTS.items():
            p = self._path(name)
            if not p.exists():
                self._write_raw(p, default)

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    @staticmethod
    def _read_raw(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            name = path.stem
            default = _DEFAULTS.get(name, {})
            return list(default) if isinstance(default, list) else dict(default)

    @staticmethod
    def _write_raw(path: Path, data):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        if sys.platform == "win32" and path.exists():
            path.unlink()
        tmp.rename(path)

    def read(self, collection: str):
        return self._read_raw(self._path(collection))

    def write(self, collection: str, data):
        self._write_raw(self._path(collection), data)

    def update(self, collection: str, fn):
        """Lock -> read -> fn(data) -> write.  Returns fn's return value."""
        path = self._path(collection)
        with FileLock(path):
            data = self._read_raw(path)
            result = fn(data)
            self._write_raw(path, data)
            return result

    def append_log(self, actor: str, action: str, summary: str):
        def _do(log):
            log.append({"actor": actor, "action": action, "summary": summary, "at": utcnow()})
            while len(log) > LOG_MAX:
                log.pop(0)
        self.update("log", _do)

    def next_task_id(self) -> int:
        def _do(meta):
            tid = meta.get("next_task_id", 1)
            meta["next_task_id"] = tid + 1
            return tid
        return self.update("meta", _do)


# ══════════════════════════════════════════════════════════════
#  IDENTITY / BANNER
# ══════════════════════════════════════════════════════════════

def _print_banner(name: str, role: str = ""):
    """Print a large, color-coded role banner to visually identify the terminal."""
    style = ROLE_STYLES.get(name, {})
    color = style.get("ansi", "\033[1;37m")
    label = style.get("label", name.upper())
    desc = style.get("desc", role)
    tab_hex = style.get("tab_hex", "")

    # Set Windows Terminal tab color (ignored by other terminals)
    if tab_hex:
        sys.stdout.write(f"\033]9;4;3;{tab_hex}\033\\")
        sys.stdout.flush()

    # Set window title
    sys.stdout.write(f"\033]0;Collab: {label}\007")
    sys.stdout.flush()

    w = 48
    bar = "=" * w
    print(f"\n{color}+{bar}+")
    print(f"|{'':^{w}}|")
    print(f"|{f'***  {label}  ***':^{w}}|")
    print(f"|{desc:^{w}}|")
    print(f"|{'':^{w}}|")
    print(f"+{bar}+{ANSI_RESET}\n")


def cmd_whoami(state: State, name: str):
    """Print the role banner for this instance."""
    nodes = state.read("nodes") or {}
    node = nodes.get(name)
    role = node["role"] if node else ""
    _print_banner(name, role)
    if node:
        print(f"  Node:   {name}")
        print(f"  Role:   {node['role']}")
        print(f"  Status: {node['status']}")
        print(f"  Joined: {node.get('joined_at', 'unknown')}")
    else:
        print(f"  Node \"{name}\" is not registered. Run: collab join {name}")


# ══════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════

# ── Nodes ─────────────────────────────────────────────────────

def cmd_join(state: State, name: str, role: str = "general"):
    def _do(nodes):
        existing = name in nodes
        nodes[name] = {
            "name": name,
            "role": role,
            "status": "active",
            "working_on": "",
            "joined_at": nodes[name]["joined_at"] if existing else utcnow(),
            "last_heartbeat": utcnow(),
            "last_poll": utcnow(),
        }
        return existing
    was_existing = state.update("nodes", _do)
    verb = "Rejoined" if was_existing else "Joined"
    state.append_log(name, "joined", f'{name} joined as "{role}"')
    _print_banner(name, role)
    print(f'[OK] {verb} as "{name}" (role: {role})')


def cmd_leave(state: State, name: str):
    def _do(nodes):
        return nodes.pop(name, None) is not None
    found = state.update("nodes", _do)
    if not found:
        print(f'[WARN] "{name}" was not registered')
        return
    # Release any file locks held by this node
    def _release(locks):
        released = [f for f, v in locks.items() if v["held_by"] == name]
        for f in released:
            del locks[f]
        return released
    released = state.update("locks", _release)
    state.append_log(name, "left", f"{name} left the collaboration")
    print(f'[OK] "{name}" has left')
    if released:
        print(f"     Released {len(released)} file lock(s)")


def cmd_status(state: State):
    nodes = state.read("nodes")
    tasks = state.read("tasks")
    ctx   = state.read("context")
    locks = state.read("locks")
    log_entries = state.read("log")

    n_open = sum(1 for t in tasks.values() if t["status"] == "open")

    print("=== Collaboration Status ===")
    print(f"    {len(nodes)} node(s) | {len(tasks)} task(s) ({n_open} open)"
          f" | {len(ctx)} context entries | {len(locks)} lock(s)\n")

    # Nodes
    print(f"Nodes ({len(nodes)}):")
    if not nodes:
        print('  (none -- run: collab join <name> --role "<role>")')
    for n in sorted(nodes.values(), key=lambda x: x.get("joined_at", "")):
        s = n.get("status", "?")
        w = trunc(n.get("working_on", "") or "-", 30)
        hb = ago(n.get("last_heartbeat", ""))
        print(f'  * {n["name"]:<15} [{s:<6}]  {w:<32} ({hb})')

    # Tasks
    print(f"\nTasks ({len(tasks)}):")
    if not tasks:
        print("  (none)")
    for t in sorted(tasks.values(), key=lambda x: x["id"]):
        icons = {"open": "o", "claimed": "*", "active": ">", "done": "v", "blocked": "x"}
        icon = icons.get(t["status"], "?")
        who = f'({t["assigned_to"]})' if t.get("assigned_to") else ""
        pri = t.get("priority", "medium")
        tag = f"  {pri.upper()}" if pri != "medium" else ""
        print(f'  #{t["id"]:<3} [{icon}] {t["status"]:<7}  {trunc(t["title"], 38):<40} {who:<15}{tag}')

    # Context
    print(f"\nShared Context ({len(ctx)}):")
    if not ctx:
        print("  (none)")
    entries = list(ctx.items())
    for k, v in entries[:15]:
        val = trunc(str(v["value"]), 45)
        by = v.get("set_by", "?")
        when = ago(v.get("set_at", ""))
        print(f"  {k} = {val}  (by {by}, {when})")
    if len(entries) > 15:
        print(f"  ... and {len(entries) - 15} more")

    # Locks
    print(f"\nFile Locks ({len(locks)}):")
    if not locks:
        print("  (none)")
    for fp, info in locks.items():
        print(f"  {fp}  ->  {info['held_by']}  ({ago(info.get('acquired_at', ''))})")

    # Recent log
    recent = log_entries[-8:]
    print("\nRecent Activity:")
    if not recent:
        print("  (none)")
    for entry in recent:
        print(f"  [{short_time(entry['at'])}] {entry['summary']}")


def cmd_heartbeat(state: State, name: str, working_on=None, node_status=None):
    def _do(nodes):
        if name not in nodes:
            return False
        nodes[name]["last_heartbeat"] = utcnow()
        if working_on is not None:
            nodes[name]["working_on"] = working_on
        if node_status is not None:
            nodes[name]["status"] = node_status
        return True
    found = state.update("nodes", _do)
    if not found:
        print(f'[ERROR] Node "{name}" not found -- join first')
        sys.exit(1)
    parts = []
    if working_on is not None:
        parts.append(f'working on: "{trunc(working_on, 40)}"')
    if node_status is not None:
        parts.append(f"status: {node_status}")
    detail = " | ".join(parts) if parts else "heartbeat"
    print(f"[OK] {name}: {detail}")


# ── Messages ──────────────────────────────────────────────────

def cmd_send(state: State, from_node: str, to_node: str, message: str):
    nodes = state.read("nodes")
    if to_node not in nodes:
        print(f'[ERROR] Node "{to_node}" not found')
        sys.exit(1)
    msg = {
        "from": from_node, "to": to_node,
        "content": message, "at": utcnow(), "type": "direct",
    }
    def _do(messages):
        messages.append(msg)
        while len(messages) > MSG_MAX:
            messages.pop(0)
    state.update("messages", _do)
    signal_node(state.dir, to_node, f"Message from {from_node}")
    state.append_log(from_node, "sent", f'{from_node} -> {to_node}: "{trunc(message, 50)}"')
    print(f'[OK] Message sent to "{to_node}"')


def cmd_broadcast(state: State, from_node: str, message: str):
    nodes = state.read("nodes")
    others = [n for n in nodes if n != from_node]
    msg = {
        "from": from_node, "to": "all",
        "content": message, "at": utcnow(), "type": "broadcast",
    }
    def _do(messages):
        messages.append(msg)
        while len(messages) > MSG_MAX:
            messages.pop(0)
    state.update("messages", _do)
    for other in others:
        signal_node(state.dir, other, f"Broadcast from {from_node}")
    state.append_log(from_node, "broadcast", f'{from_node} -> all: "{trunc(message, 50)}"')
    print(f"[OK] Broadcast sent ({len(others)} other node(s))")


def cmd_inbox(state: State, name: str, show_all: bool = False, limit: int = 20):
    messages = state.read("messages")
    nodes = state.read("nodes")
    last_poll = nodes.get(name, {}).get("last_poll", "1970-01-01T00:00:00+00:00")

    relevant = [
        m for m in messages
        if m["to"] == name or (m["to"] == "all" and m["from"] != name)
    ]
    if not show_all:
        relevant = [m for m in relevant if m["at"] > last_poll]
    relevant = relevant[-limit:]

    if not relevant:
        print("No new messages." if not show_all else "No messages.")
        return

    label = "All messages" if show_all else "New messages"
    print(f'{label} for "{name}" ({len(relevant)}):\n')
    for m in relevant:
        t = short_time(m["at"])
        src = m["from"]
        tag = "broadcast" if m["to"] == "all" else "-> you"
        print(f"  [{t}] {src} ({tag}): {m['content']}")


# ── Context ───────────────────────────────────────────────────

def cmd_context_set(state: State, key: str, value: str, by: str = "system"):
    def _do(ctx):
        ctx[key] = {"value": value, "set_by": by, "set_at": utcnow()}
    state.update("context", _do)
    state.append_log(by, "context_set", f'{by} set context "{key}"')
    print(f'[OK] Context "{key}" set by {by}')


def cmd_context_get(state: State, key=None):
    ctx = state.read("context")
    if key:
        if key not in ctx:
            print(f'[ERROR] Key "{key}" not found')
            sys.exit(1)
        e = ctx[key]
        print(f"Key:   {key}")
        print(f"Value: {e['value']}")
        print(f"Set:   {e.get('set_by', '?')} ({ago(e.get('set_at', ''))})")
    else:
        if not ctx:
            print("No shared context.")
            return
        print(f"Shared Context ({len(ctx)}):\n")
        for k, v in ctx.items():
            print(f"  {k} = {trunc(str(v['value']), 50)}")
            print(f"    (by {v.get('set_by', '?')}, {ago(v.get('set_at', ''))})")


def cmd_context_del(state: State, key: str):
    def _do(ctx):
        return ctx.pop(key, None) is not None
    found = state.update("context", _do)
    if not found:
        print(f'[ERROR] Key "{key}" not found')
        sys.exit(1)
    state.append_log("system", "context_del", f'Deleted context "{key}"')
    print(f'[OK] Context "{key}" deleted')


def cmd_context_append(state: State, key: str, value: str, by: str = "system"):
    def _do(ctx):
        if key in ctx:
            old = ctx[key]["value"]
            ctx[key] = {"value": old + "\n" + value, "set_by": by, "set_at": utcnow()}
        else:
            ctx[key] = {"value": value, "set_by": by, "set_at": utcnow()}
    state.update("context", _do)
    state.append_log(by, "context_append", f'{by} appended to context "{key}"')
    print(f'[OK] Appended to context "{key}"')


# ── Tasks ─────────────────────────────────────────────────────

def cmd_task_add(state: State, title: str, desc: str = "", assign: str = "",
                 priority: str = "medium", by: str = "system"):
    tid = state.next_task_id()
    task = {
        "id": tid, "title": title, "description": desc,
        "status": "claimed" if assign else "open",
        "priority": priority, "created_by": by,
        "assigned_to": assign or None,
        "created_at": utcnow(), "updated_at": utcnow(),
        "result": "",
        "history": [{"action": "created", "by": by, "at": utcnow()}],
    }
    if assign:
        task["history"].append({"action": f"assigned to {assign}", "by": by, "at": utcnow()})
    def _do(tasks):
        tasks[str(tid)] = task
    state.update("tasks", _do)

    summary = f'Task #{tid} created: "{trunc(title, 40)}"'
    if assign:
        summary += f" (assigned to {assign})"
        signal_node(state.dir, assign, f"Task #{tid} assigned to you by {by}")
    state.append_log(by, "task_created", summary)
    print(f"[OK] {summary}")


def cmd_task_list(state: State, status_filter=None, assigned_filter=None):
    tasks = state.read("tasks")
    items = sorted(tasks.values(), key=lambda t: t["id"])
    if status_filter:
        items = [t for t in items if t["status"] == status_filter]
    if assigned_filter:
        items = [t for t in items if t.get("assigned_to") == assigned_filter]
    if not items:
        print("No tasks found.")
        return

    print(f"Tasks ({len(items)}):\n")
    for t in items:
        icons = {"open": "o", "claimed": "*", "active": ">", "done": "v", "blocked": "x"}
        icon = icons.get(t["status"], "?")
        who = f'({t["assigned_to"]})' if t.get("assigned_to") else ""
        pri = t.get("priority", "medium")
        tag = f"  {pri.upper()}" if pri != "medium" else ""
        print(f'  #{t["id"]:<3} [{icon}] {t["status"]:<7}  {trunc(t["title"], 38):<40} {who:<15}{tag}')


def cmd_task_claim(state: State, name: str, task_id: int):
    def _do(tasks):
        k = str(task_id)
        if k not in tasks:
            return "not_found"
        t = tasks[k]
        if t["status"] == "done":
            return "already_done"
        t["status"] = "claimed"
        t["assigned_to"] = name
        t["updated_at"] = utcnow()
        t["history"].append({"action": f"claimed by {name}", "by": name, "at": utcnow()})
        return "ok"
    result = state.update("tasks", _do)
    if result == "not_found":
        print(f"[ERROR] Task #{task_id} not found")
        sys.exit(1)
    if result == "already_done":
        print(f"[ERROR] Task #{task_id} is already done")
        sys.exit(1)
    state.append_log(name, "task_claimed", f"{name} claimed task #{task_id}")
    print(f'[OK] "{name}" claimed task #{task_id}')


def cmd_task_update(state: State, task_id: int, new_status: str,
                    result_text: str = "", by: str = "system"):
    def _do(tasks):
        k = str(task_id)
        if k not in tasks:
            return "not_found"
        t = tasks[k]
        old = t["status"]
        t["status"] = new_status
        t["updated_at"] = utcnow()
        if result_text:
            t["result"] = result_text
        t["history"].append({"action": f"{old} -> {new_status}", "by": by, "at": utcnow()})
        return "ok"
    result = state.update("tasks", _do)
    if result == "not_found":
        print(f"[ERROR] Task #{task_id} not found")
        sys.exit(1)
    log_msg = f"Task #{task_id} -> {new_status}"
    if result_text:
        log_msg += f': "{trunc(result_text, 40)}"'
    state.append_log(by, "task_updated", log_msg)
    print(f"[OK] Task #{task_id} -> {new_status}")


def cmd_task_show(state: State, task_id: int):
    tasks = state.read("tasks")
    k = str(task_id)
    if k not in tasks:
        print(f"[ERROR] Task #{task_id} not found")
        sys.exit(1)
    t = tasks[k]
    print(f'Task #{t["id"]}: {t["title"]}')
    print(f'  Status:      {t["status"]}')
    print(f'  Priority:    {t.get("priority", "medium")}')
    print(f'  Assigned:    {t.get("assigned_to") or "(unassigned)"}')
    print(f'  Created by:  {t.get("created_by", "?")} ({ago(t.get("created_at", ""))})')
    if t.get("description"):
        print(f'  Description: {t["description"]}')
    if t.get("result"):
        print(f'  Result:      {t["result"]}')
    print("  History:")
    for h in t.get("history", []):
        print(f"    [{short_time(h['at'])}] {h['action']} (by {h.get('by', '?')})")


# ── File Locks ────────────────────────────────────────────────

def cmd_lock(state: State, name: str, filepath: str):
    def _do(locks):
        if filepath in locks:
            holder = locks[filepath]["held_by"]
            return "yours" if holder == name else f"held:{holder}"
        locks[filepath] = {"held_by": name, "acquired_at": utcnow()}
        return "ok"
    result = state.update("locks", _do)
    if result == "yours":
        print(f'[OK] Already locked by you: "{filepath}"')
        return
    if result.startswith("held:"):
        print(f'[ERROR] "{filepath}" locked by "{result[5:]}"')
        sys.exit(1)
    state.append_log(name, "locked", f'{name} locked "{filepath}"')
    print(f'[OK] Locked "{filepath}"')


def cmd_unlock(state: State, name: str, filepath: str):
    def _do(locks):
        if filepath not in locks:
            return "not_locked"
        if locks[filepath]["held_by"] != name:
            return f"held:{locks[filepath]['held_by']}"
        del locks[filepath]
        return "ok"
    result = state.update("locks", _do)
    if result == "not_locked":
        print(f'[OK] "{filepath}" was not locked')
        return
    if result.startswith("held:"):
        print(f'[ERROR] "{filepath}" locked by "{result[5:]}", not you')
        sys.exit(1)
    state.append_log(name, "unlocked", f'{name} unlocked "{filepath}"')
    print(f'[OK] Unlocked "{filepath}"')


def cmd_locks(state: State):
    locks = state.read("locks")
    if not locks:
        print("No active file locks.")
        return
    print(f"File Locks ({len(locks)}):\n")
    for fp, info in locks.items():
        print(f"  {fp}  ->  {info['held_by']}  ({ago(info.get('acquired_at', ''))})")


# ── Poll ──────────────────────────────────────────────────────

def cmd_poll(state: State, name: str):
    nodes = state.read("nodes")
    if name not in nodes:
        print(f'[ERROR] Node "{name}" not found -- join first')
        sys.exit(1)

    # Clear any pending signal file
    read_and_clear_signal(state.dir, name)

    last_poll = nodes[name].get("last_poll", "1970-01-01T00:00:00+00:00")
    messages  = state.read("messages")
    log_data  = state.read("log")
    tasks     = state.read("tasks")

    # New messages addressed to this node (or broadcast)
    new_msgs = [
        m for m in messages
        if m["at"] > last_poll
        and (m["to"] == name or (m["to"] == "all" and m["from"] != name))
    ]

    # Activity by OTHER nodes since last poll
    new_activity = [
        e for e in log_data
        if e["at"] > last_poll and e.get("actor") != name
    ]

    # Advance last_poll + heartbeat
    def _do(nodes):
        if name in nodes:
            nodes[name]["last_poll"] = utcnow()
            nodes[name]["last_heartbeat"] = utcnow()
    state.update("nodes", _do)

    if not new_msgs and not new_activity:
        print("No updates since last check.")
        return

    print(f'=== Updates for "{name}" ===\n')

    if new_msgs:
        print(f"Messages ({len(new_msgs)}):")
        for m in new_msgs:
            src = m["from"]
            tag = "broadcast" if m["to"] == "all" else "-> you"
            print(f"  [{short_time(m['at'])}] {src} ({tag}): {m['content']}")
        print()

    # Non-message activity from others
    other = [e for e in new_activity if e["action"] not in ("sent", "broadcast")]
    if other:
        print(f"Activity ({len(other)}):")
        for e in other[-25:]:
            print(f"  [{short_time(e['at'])}] {e['summary']}")
        print()

    # Quick summary line
    active_nodes = [n for n in nodes.values() if n.get("status") in ("active", "busy")]
    open_tasks = [t for t in tasks.values() if t["status"] == "open"]
    my_tasks = [t for t in tasks.values()
                if t.get("assigned_to") == name and t["status"] not in ("done",)]
    print(f"Summary: {len(active_nodes)} active nodes, "
          f"{len(open_tasks)} open tasks, {len(my_tasks)} assigned to you")


# ── Pending (lightweight notification check) ─────────────────

def cmd_pending(state: State, name: str):
    """Ultra-fast check: do I have anything waiting? Returns signal lines + counts."""
    signals = read_and_clear_signal(state.dir, name)
    nodes = state.read("nodes")
    if name not in nodes:
        print(f'[ERROR] Node "{name}" not found -- join first')
        sys.exit(1)

    last_poll = nodes[name].get("last_poll", "1970-01-01T00:00:00+00:00")
    messages = state.read("messages")
    tasks = state.read("tasks")

    new_msgs = [
        m for m in messages
        if m["at"] > last_poll
        and (m["to"] == name or (m["to"] == "all" and m["from"] != name))
    ]
    my_pending = [
        t for t in tasks.values()
        if t.get("assigned_to") == name and t["status"] in ("open", "claimed")
    ]

    total = len(new_msgs) + len(my_pending)
    if signals:
        print(f"[!] {len(signals)} signal(s):")
        for s in signals[-5:]:
            print(f"  {s}")
    if new_msgs:
        print(f"[!] {len(new_msgs)} new message(s) — run `poll {name}` to read")
    if my_pending:
        print(f"[!] {len(my_pending)} task(s) waiting for you")
    if total == 0 and not signals:
        print("[ok] Nothing pending.")


# ── Log ───────────────────────────────────────────────────────

def cmd_log(state: State, limit: int = 20):
    log_data = state.read("log")
    if not log_data:
        print("No activity yet.")
        return
    entries = log_data[-limit:]
    print(f"Activity Log (last {len(entries)}):\n")
    for e in entries:
        print(f"  [{short_time(e['at'])}] {e['summary']}")


# ── Request ───────────────────────────────────────────────────

def cmd_request(state: State, from_node: str, to_node: str, description: str):
    nodes = state.read("nodes")
    if to_node not in nodes:
        print(f'[ERROR] Node "{to_node}" not found')
        sys.exit(1)

    # Create an assigned task
    tid = state.next_task_id()
    task = {
        "id": tid, "title": description,
        "description": f"Requested by {from_node}",
        "status": "claimed", "priority": "high",
        "created_by": from_node, "assigned_to": to_node,
        "created_at": utcnow(), "updated_at": utcnow(),
        "result": "",
        "history": [
            {"action": "created", "by": from_node, "at": utcnow()},
            {"action": f"assigned to {to_node}", "by": from_node, "at": utcnow()},
        ],
    }
    def _do_task(tasks):
        tasks[str(tid)] = task
    state.update("tasks", _do_task)

    # Send a message too
    msg = {
        "from": from_node, "to": to_node,
        "content": f"[Request] {description} (task #{tid})",
        "at": utcnow(), "type": "request",
    }
    def _do_msg(messages):
        messages.append(msg)
        while len(messages) > MSG_MAX:
            messages.pop(0)
    state.update("messages", _do_msg)

    signal_node(state.dir, to_node, f"Request from {from_node}: task #{tid}")
    state.append_log(from_node, "request",
                     f'{from_node} requested {to_node}: "{trunc(description, 40)}" (task #{tid})')
    print(f'[OK] Request sent to "{to_node}" as task #{tid}')


# ── Window Control Commands ───────────────────────────────────

def cmd_inject(state: State, target_node: str, prompt: str):
    """Type a prompt into the target node's terminal and press Enter."""
    pid = find_collab_window(target_node)
    if not pid:
        print(f'[ERROR] No console found for "{target_node}"')
        print(f'  Could not find cmd.exe with _run_{target_node}.bat')
        sys.exit(1)

    print(f'  Found {target_node} console: PID {pid}')
    print(f'  Injecting: {trunc(prompt, 80)}')

    if _run_injector(pid, "text", prompt):
        state.append_log("lead", "inject",
                         f'Injected prompt to {target_node}: "{trunc(prompt, 40)}"')
        print(f'[OK] Prompt sent to "{target_node}"')
    else:
        print(f'[ERROR] Failed to inject into "{target_node}"')
        sys.exit(1)


def cmd_interrupt(state: State, target_node: str):
    """Send Escape to the target node's console to stop generation."""
    pid = find_collab_window(target_node)
    if not pid:
        print(f'[ERROR] No console found for "{target_node}"')
        sys.exit(1)

    print(f'  Found {target_node} console: PID {pid}')

    if _run_injector(pid, "escape"):
        state.append_log("lead", "interrupt", f'Interrupted {target_node} (sent Escape)')
        print(f'[OK] Escape sent to "{target_node}"')
    else:
        print(f'[ERROR] Failed to send Escape to "{target_node}"')
        sys.exit(1)


def cmd_nudge(state: State, target_node: str, message: str = ""):
    """Send a signal + inject a poll command into the target's console."""
    # Always write a signal file
    reason = message if message else "Nudge from lead"
    signal_node(state.dir, target_node, reason)

    # If a message was provided, send it via collab system too
    if message:
        msg = {
            "from": "lead", "to": target_node,
            "content": message, "at": utcnow(), "type": "nudge",
        }
        def _do(messages):
            messages.append(msg)
            while len(messages) > MSG_MAX:
                messages.pop(0)
        state.update("messages", _do)

    pid = find_collab_window(target_node)
    if pid:
        print(f'  Found {target_node} console: PID {pid}')
        # Inject a poll command so they see their updates
        collab_path = str(SCRIPT_PATH).replace("\\", "/")
        poll_cmd = f'python "{collab_path}" poll {target_node}'
        if _run_injector(pid, "text", poll_cmd):
            state.append_log("lead", "nudge", f'Nudged {target_node} (console + signal)')
            print(f'[OK] Nudged "{target_node}" (signal + poll injected)')
        else:
            state.append_log("lead", "nudge", f'Nudged {target_node} (signal only, inject failed)')
            print(f'[WARN] Signal sent but console injection failed for "{target_node}"')
    else:
        state.append_log("lead", "nudge", f'Nudged {target_node} (signal only, no console)')
        print(f'[WARN] Signal sent but no console found for "{target_node}"')
        print(f'  The node will see the signal next time it runs "pending"')


def cmd_windows(state: State):
    """List all detectable collaboration consoles."""
    role_pids = _get_role_cmd_pids() if sys.platform == "win32" else {}
    nodes = state.read("nodes") or {}
    # Show all known roles — both registered nodes and detected consoles
    all_names = set(nodes.keys()) | set(role_pids.keys())
    if not all_names:
        print("No nodes registered and no consoles detected.")
        return
    print("Collaboration Consoles:\n")
    for name in sorted(all_names):
        pid = role_pids.get(name)
        registered = name in nodes
        if pid:
            tag = "[FOUND]" if registered else "[FOUND - not joined]"
            print(f"  {name:<12} {tag}  cmd.exe PID {pid}")
        else:
            print(f"  {name:<12} [NOT FOUND]")


# ── Reset ─────────────────────────────────────────────────────

def cmd_reset(state: State, confirm: bool = False):
    if not confirm:
        print("[ERROR] This will delete ALL collaboration state.")
        print("        Use --confirm to proceed.")
        sys.exit(1)
    shutil.rmtree(state.dir, ignore_errors=True)
    state.__init__(state.dir)
    print("[OK] All collaboration state has been cleared")


# ══════════════════════════════════════════════════════════════
#  CLI PARSER
# ══════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="collab",
        description="Claude Code Collaboration Harness -- real-time multi-instance coordination",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  collab join architect --role "system design"
  collab send architect coder "The API schema is ready for review"
  collab broadcast architect "Database migration complete"
  collab poll coder
  collab task add "Implement auth" --assign coder --priority high --by architect
  collab context set "db_type" "postgresql" --by architect
  collab request architect coder "Please review the schema in docs/schema.md"
  collab status
""",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--state-dir", default=None, help="Override state directory path")
    sub = p.add_subparsers(dest="command", help="Available commands")

    # ── join ──
    j = sub.add_parser("join", help="Join the collaboration session")
    j.add_argument("name", help="Unique node name (e.g. architect, backend, frontend)")
    j.add_argument("--role", default="general", help="Role description")

    # ── leave ──
    lv = sub.add_parser("leave", help="Leave the collaboration (releases your locks)")
    lv.add_argument("name", help="Your node name")

    # ── status ──
    sub.add_parser("status", help="Full overview: nodes, tasks, context, locks, activity")

    # ── heartbeat ──
    hb = sub.add_parser("heartbeat", help="Update your working status")
    hb.add_argument("name", help="Your node name")
    hb.add_argument("--working-on", dest="working_on", default=None,
                    help="What you're currently doing")
    hb.add_argument("--status", dest="node_status", default=None,
                    choices=["active", "idle", "busy", "away"])

    # ── send ──
    sd = sub.add_parser("send", help="Send a direct message")
    sd.add_argument("from_node", metavar="from", help="Your node name")
    sd.add_argument("to", help="Target node name")
    sd.add_argument("message", help="Message content")

    # ── broadcast ──
    bc = sub.add_parser("broadcast", help="Message all nodes")
    bc.add_argument("from_node", metavar="from", help="Your node name")
    bc.add_argument("message", help="Message content")

    # ── inbox ──
    ib = sub.add_parser("inbox", help="View your messages")
    ib.add_argument("name", help="Your node name")
    ib.add_argument("--all", action="store_true", dest="show_all",
                    help="Show all messages, not just since last poll")
    ib.add_argument("--limit", type=int, default=20, help="Max messages")

    # ── context ──
    cx = sub.add_parser("context", help="Shared key-value context store")
    cx_sub = cx.add_subparsers(dest="context_cmd")

    cs = cx_sub.add_parser("set", help="Set a context value")
    cs.add_argument("key")
    cs.add_argument("value")
    cs.add_argument("--by", default="system", help="Your node name")

    cg = cx_sub.add_parser("get", help="Get context value(s)")
    cg.add_argument("key", nargs="?", default=None, help="Key (omit for all)")

    cd = cx_sub.add_parser("del", help="Delete a context key")
    cd.add_argument("key")

    ca = cx_sub.add_parser("append", help="Append to an existing context value")
    ca.add_argument("key")
    ca.add_argument("value")
    ca.add_argument("--by", default="system", help="Your node name")

    # ── task ──
    tk = sub.add_parser("task", help="Shared task board")
    tk_sub = tk.add_subparsers(dest="task_cmd")

    ta = tk_sub.add_parser("add", help="Create a new task")
    ta.add_argument("title")
    ta.add_argument("--desc", default="", help="Detailed description")
    ta.add_argument("--assign", default="", help="Assign to a node")
    ta.add_argument("--priority", default="medium",
                    choices=["low", "medium", "high", "critical"])
    ta.add_argument("--by", default="system", help="Your node name")

    tl = tk_sub.add_parser("list", help="List tasks")
    tl.add_argument("--status", default=None, help="Filter: open/claimed/active/done/blocked")
    tl.add_argument("--assigned", default=None, help="Filter by assignee")

    tc = tk_sub.add_parser("claim", help="Claim an open task")
    tc.add_argument("name", help="Your node name")
    tc.add_argument("task_id", type=int, help="Task ID")

    tu = tk_sub.add_parser("update", help="Update task status")
    tu.add_argument("task_id", type=int)
    tu.add_argument("new_status",
                    choices=["open", "claimed", "active", "done", "blocked"])
    tu.add_argument("--result", default="", help="Result or notes")
    tu.add_argument("--by", default="system", help="Your node name")

    ts = tk_sub.add_parser("show", help="Show full task details")
    ts.add_argument("task_id", type=int)

    # ── lock / unlock / locks ──
    lk = sub.add_parser("lock", help="Lock a file before editing")
    lk.add_argument("name", help="Your node name")
    lk.add_argument("file", help="File path to lock")

    ul = sub.add_parser("unlock", help="Release a file lock")
    ul.add_argument("name", help="Your node name")
    ul.add_argument("file", help="File path to unlock")

    sub.add_parser("locks", help="List all active file locks")

    # ── pending ──
    pd = sub.add_parser("pending", help="Quick check: any signals, messages, or tasks waiting?")
    pd.add_argument("name", help="Your node name")

    # ── poll ──
    pl = sub.add_parser("poll", help="Get all updates since your last poll")
    pl.add_argument("name", help="Your node name")

    # ── log ──
    lg = sub.add_parser("log", help="View the activity log")
    lg.add_argument("--limit", type=int, default=20, help="Number of entries")

    # ── request ──
    rq = sub.add_parser("request", help="Request work from another node (task + message)")
    rq.add_argument("from_node", metavar="from", help="Your node name")
    rq.add_argument("to", help="Target node name")
    rq.add_argument("description", help="What you need done")

    # ── window control ──
    inj = sub.add_parser("inject", help="Type a prompt into a node's terminal window")
    inj.add_argument("target", help="Target node name (e.g. dev1)")
    inj.add_argument("prompt", help="Text to type (will press Enter after)")

    intr = sub.add_parser("interrupt", help="Send Escape to a node's window (stop generation)")
    intr.add_argument("target", help="Target node name")

    ndg = sub.add_parser("nudge", help="Signal + inject a poll command into a node's window")
    ndg.add_argument("target", help="Target node name")
    ndg.add_argument("message", nargs="?", default="", help="Optional message to send")

    sub.add_parser("windows", help="List all detectable collaboration windows")

    # ── whoami ──
    wh = sub.add_parser("whoami", help="Print the role banner to identify this terminal")
    wh.add_argument("name", help="Your node name")

    # ── reset ──
    rs = sub.add_parser("reset", help="Clear ALL collaboration state")
    rs.add_argument("--confirm", action="store_true", help="Required to confirm reset")

    return p


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    state_dir = Path(args.state_dir) if args.state_dir else DEFAULT_STATE_DIR
    state = State(state_dir)

    try:
        cmd = args.command

        if cmd == "join":
            cmd_join(state, args.name, args.role)
        elif cmd == "leave":
            cmd_leave(state, args.name)
        elif cmd == "status":
            cmd_status(state)
        elif cmd == "heartbeat":
            cmd_heartbeat(state, args.name, args.working_on, args.node_status)
        elif cmd == "send":
            cmd_send(state, args.from_node, args.to, args.message)
        elif cmd == "broadcast":
            cmd_broadcast(state, args.from_node, args.message)
        elif cmd == "inbox":
            cmd_inbox(state, args.name, args.show_all, args.limit)
        elif cmd == "context":
            cc = args.context_cmd
            if not cc:
                print("[ERROR] Specify subcommand: set | get | del | append")
                sys.exit(1)
            {"set":    lambda: cmd_context_set(state, args.key, args.value, args.by),
             "get":    lambda: cmd_context_get(state, args.key),
             "del":    lambda: cmd_context_del(state, args.key),
             "append": lambda: cmd_context_append(state, args.key, args.value, args.by),
            }[cc]()
        elif cmd == "task":
            tc = args.task_cmd
            if not tc:
                print("[ERROR] Specify subcommand: add | list | claim | update | show")
                sys.exit(1)
            {"add":    lambda: cmd_task_add(state, args.title, args.desc, args.assign,
                                            args.priority, args.by),
             "list":   lambda: cmd_task_list(state, args.status, args.assigned),
             "claim":  lambda: cmd_task_claim(state, args.name, args.task_id),
             "update": lambda: cmd_task_update(state, args.task_id, args.new_status,
                                                args.result, args.by),
             "show":   lambda: cmd_task_show(state, args.task_id),
            }[tc]()
        elif cmd == "lock":
            cmd_lock(state, args.name, args.file)
        elif cmd == "unlock":
            cmd_unlock(state, args.name, args.file)
        elif cmd == "locks":
            cmd_locks(state)
        elif cmd == "pending":
            cmd_pending(state, args.name)
        elif cmd == "poll":
            cmd_poll(state, args.name)
        elif cmd == "log":
            cmd_log(state, args.limit)
        elif cmd == "request":
            cmd_request(state, args.from_node, args.to, args.description)
        elif cmd == "inject":
            cmd_inject(state, args.target, args.prompt)
        elif cmd == "interrupt":
            cmd_interrupt(state, args.target)
        elif cmd == "nudge":
            cmd_nudge(state, args.target, args.message)
        elif cmd == "windows":
            cmd_windows(state)
        elif cmd == "whoami":
            cmd_whoami(state, args.name)
        elif cmd == "reset":
            cmd_reset(state, args.confirm)

    except TimeoutError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
