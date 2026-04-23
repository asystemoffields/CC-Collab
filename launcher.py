#!/usr/bin/env python3
"""
Claude Code Collaboration Launcher
===================================
Launches N Claude Code instances for real-time collaboration.

Usage:
    python launcher.py [project_directory] [-n NODES]
    python launcher.py /path/to/project --nodes 5
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

COLLAB_DIR = Path(__file__).parent.resolve()
COLLAB_PY  = COLLAB_DIR / "collab.py"
STATE_DIR  = COLLAB_DIR / "state"

# Bash-friendly path (forward slashes work in Git Bash on Windows)
COLLAB_PY_BASH = str(COLLAB_PY).replace("\\", "/")

# Default 3-node setup — overridden by --nodes N
DEFAULT_ROLES = [
    ("lead", "Coordination, architecture, and task management"),
    ("dev1",  "Primary implementation and development"),
    ("dev2",  "Secondary development, review, and testing"),
]

# Color palette — lead is always gold; dev nodes cycle through these
_DEV_COLORS = [
    {"fg": "[1;36m", "tab": "#00B4D8", "icon": "***"},  # cyan
    {"fg": "[1;32m", "tab": "#2DC653", "icon": "***"},  # green
    {"fg": "[1;35m", "tab": "#B07CD8", "icon": "***"},  # purple
    {"fg": "[1;31m", "tab": "#E06C60", "icon": "***"},  # red
    {"fg": "[1;34m", "tab": "#4A90D9", "icon": "***"},  # blue
    {"fg": "[1;37m", "tab": "#A0A0A0", "icon": "***"},  # white/silver
    {"fg": "[1;33m", "tab": "#D4A017", "icon": "***"},  # dark gold
    {"fg": "[1;36m", "tab": "#20B2AA", "icon": "***"},  # teal
]

LEAD_COLOR = {"fg": "[1;33m", "tab": "#E5A00D", "icon": "***"}  # yellow/gold


def build_roles(n: int) -> list:
    """Generate role list for N nodes: 1 lead + (N-1) devs."""
    roles = [("lead", "Coordination, architecture, and task management")]
    for i in range(1, n):
        if i == 1:
            desc = "Primary implementation and development"
        elif i == 2:
            desc = "Secondary development, review, and testing"
        else:
            desc = f"Development node {i}"
        roles.append((f"dev{i}", desc))
    return roles


def get_role_color(role_name: str) -> dict:
    """Get color config for a role name."""
    if role_name == "lead":
        return LEAD_COLOR
    # Extract dev number and cycle through palette
    try:
        idx = int(role_name.replace("dev", "")) - 1
    except ValueError:
        idx = 0
    return _DEV_COLORS[idx % len(_DEV_COLORS)]


CLAUDE_MODEL = os.environ.get("COLLAB_MODEL", "opus")
SKIP_PERMISSIONS = os.environ.get("COLLAB_SKIP_PERMISSIONS", "1") == "1"

# Model capability tier: "full" for Opus, "lite" for Haiku/Sonnet
# "lite" simplifies CLAUDE.md instructions, adds explicit step-by-step guidance,
# reduces command surface, and adds error recovery hints
COLLAB_TIER = os.environ.get("COLLAB_TIER", "auto")  # auto|full|lite

COLLAB_MARKER = "<!-- COLLAB:AUTO -->"

# Wizard model menu — bare aliases at top stay current automatically;
# pinned IDs below for determinism. Edit when new families ship.
MODEL_MENU = [
    ("opus",       "Latest Opus (recommended)"),
    ("sonnet",     "Latest Sonnet"),
    ("haiku",      "Latest Haiku  [no /effort support]"),
    ("claude-opus-4-7",          "Opus 4.7 (pinned)"),
    ("claude-opus-4-6",          "Opus 4.6 (pinned)"),
    ("claude-sonnet-4-6",        "Sonnet 4.6 (pinned)"),
    ("claude-haiku-4-5-20251001","Haiku 4.5 (pinned)"),
]


def _detect_tier(model: str) -> str:
    """Detect capability tier from model name."""
    model_lower = model.lower()
    if "opus" in model_lower:
        return "full"
    # Sonnet and Haiku get the simplified protocol
    return "lite"


def _session_tier(lead_model: str, dev_model: str) -> str:
    """Tier for a mixed-model session — weakest reader wins."""
    if _detect_tier(lead_model) == "lite" or _detect_tier(dev_model) == "lite":
        return "lite"
    return "full"


def _supports_effort(model: str) -> bool:
    """Haiku does not support /effort; everything else (opus/sonnet) does."""
    return "haiku" not in model.lower()


def collab_md_section(num_nodes: int = 3) -> str:
    """Generate the CLAUDE.md block that teaches each instance how to collaborate."""
    roles = build_roles(num_nodes)
    dev_names = [name for name, _ in roles if name != "lead"]
    join_lines = []
    for name, _ in roles:
        if name == "lead":
            join_lines.append(f'   - If `lead`: `python "{COLLAB_PY_BASH}" join lead --role "Coordination, architecture, task management"`')
        else:
            idx = name.replace("dev", "")
            join_lines.append(f'   - If `{name}`: `python "{COLLAB_PY_BASH}" join {name} --role "Development node {idx}"`')
    join_block = "\n".join(join_lines)
    dev_list = ", ".join(f"`{d}`" for d in dev_names)
    p = COLLAB_PY_BASH
    return f"""{COLLAB_MARKER}
## Multi-Instance Collaboration

> Run `echo $COLLAB_ROLE`. If empty, **SKIP this entire section.**

You are 1 of {num_nodes} concurrent Claude Code instances. Coordinate via shared state.

### Startup (run FIRST, before any other work)

```bash
echo $COLLAB_ROLE    # your role — if empty, stop here
```
Join:
{join_block}
Then: `python "{p}" status` and `python "{p}" poll <you>`

**Lead** ({dev_list} report to you): create tasks, assign work, monitor, unblock.
**Dev**: poll for tasks → claim → work → mark done → poll again. Self-direct when idle.

### Commands

`p`=poll `s`=status `b`=broadcast `t`=task `c`=context `d`=diff `h`=health `n`=nudge

**Core loop** — lock before edit, unlock after, poll for updates:
```
python "{p}" poll <you>                                    # all updates since last poll
python "{p}" lock <you> "<file>"                           # before editing
python "{p}" unlock <you> "<file>"                         # after editing (broadcasts diff)
python "{p}" task update <id> active --by <you>            # start task
python "{p}" task update <id> done --result "..." --by <you>  # finish task
```

**Communication** (auto-pushed to target terminal):
```
python "{p}" send <you> <them> "<msg>"                     # direct message
python "{p}" broadcast <you> "<msg>"                       # message all
python "{p}" btw <you> <them|all> "<msg>"                  # async FYI, no reply needed
```

**Tasks & context:**
```
python "{p}" task add "<title>" --assign <node> --priority high --by <you>
python "{p}" task list [--status open] [--assigned <you>]
python "{p}" context set "<key>" "<val>" --by <you>
```

**Awareness:** `diff <you>` `status` `health` `locks` `reap`

**Lead terminal control:** `nudge <target> "<msg>"` (safest) | `inject <target> "<prompt>"` | `interrupt <target>` (Esc×2, use before inject if mid-generation)

### Behavior

**Lead**: assign tasks → nudge devs → do own work → poll progress → reassign/unblock. Use `interrupt` then `inject` to redirect. Prefer `nudge` for routine check-ins.
**Dev**: follow lead instructions first. If idle: claim open tasks → create own → broadcast intent. Lock files, don't make arch decisions solo, share context via `context set`.
**All**: messages auto-push to terminals. Poll when signals appear. Completion triggers auto-notify blocked dependents. If node crashes, `reap` reclaims its resources.

Full reference: {str(COLLAB_DIR / 'PROTOCOL.md').replace(chr(92), '/')}
{COLLAB_MARKER}"""


_LEAD_PLAYBOOK_BLOCK = """
### Lead playbook (full tier — only the lead reads this)

You are the autonomous manager. Loop:

1. Plan: break the user's request into tasks; assign to dev1/dev2/...
2. Build your own piece (architecture, shared files, the hard part)
3. Monitor: `poll lead` and `task list` regularly
4. Steer: if a dev drifts, `interrupt <them>` (Esc x2), wait ~2s, then `inject <them> "Stop. Do X instead."`
5. Nudge: when assigning new work, `nudge <target> "<msg>"` so they notice without interrupting

Terminal control: `nudge` (safest) | `inject` (types text + Enter) | `interrupt` (Esc x2). Always `interrupt` before `inject` if the target is mid-generation. Never `inject` while a dev holds a file lock.
"""


def collab_md_section_lite(num_nodes: int = 3, with_lead_playbook: bool = False) -> str:
    """Generate a simplified CLAUDE.md block for less capable models (Haiku/Sonnet).

    Key differences from full tier:
    - Fewer commands to remember (essential 6 only)
    - Explicit step-by-step format with numbered actions
    - Error recovery instructions built in
    - No lead playbook (lite devs are always workers)
    - Shorter, clearer language
    """
    roles = build_roles(num_nodes)
    dev_names = [name for name, _ in roles if name != "lead"]
    join_lines = []
    for name, _ in roles:
        if name == "lead":
            join_lines.append(f'   - If `lead`: `python "{COLLAB_PY_BASH}" join lead --role "Lead"`')
        else:
            idx = name.replace("dev", "")
            join_lines.append(f'   - If `{name}`: `python "{COLLAB_PY_BASH}" join {name} --role "Developer {idx}"`')
    join_block = "\n".join(join_lines)
    p = COLLAB_PY_BASH

    lead_section = _LEAD_PLAYBOOK_BLOCK if with_lead_playbook else ""

    return f"""{COLLAB_MARKER}
## Multi-Instance Collaboration

> Run `echo $COLLAB_ROLE`. If empty, **SKIP this entire section.**

You are 1 of {num_nodes} Claude Code instances. Coordinate via shared state.

### Startup (DO THIS FIRST)

```bash
echo $COLLAB_ROLE
```
Join:
{join_block}
Then: `python "{p}" --brief poll <your-name>`

### Workflow Loop

1. `python "{p}" --brief poll <you>` — check for tasks/messages
2. `python "{p}" lock <you> "<file>"` — before editing
3. Do the work
4. `python "{p}" unlock <you> "<file>"` — after editing
5. `python "{p}" task update <id> done --result "..." --by <you>`
6. Back to step 1

**Extra:** `broadcast <you> "<msg>"` to tell everyone something. `task list --status open` to find unclaimed work. Use `--brief` on poll.

**Rules:** Always lock before edit. Never edit locked files (`locks` to check). Poll after every task. If idle: claim open tasks or create your own. Lead instructions override self-direction.

**If lead:** also `task add "<title>" --assign <node> --by lead` | `--brief status` | `nudge <target> "<msg>"`
{lead_section}
Ref: {str(COLLAB_DIR / 'PROTOCOL.md').replace(chr(92), '/')}
{COLLAB_MARKER}"""


_BACKUP_NAME = "_claude_md_backup"


def pre_trust_directory(project_dir: Path):
    """Pre-accept the Claude Code trust dialog for a directory.
    Writes hasTrustDialogAccepted=true into ~/.claude.json so
    instances launched into this directory skip the trust prompt."""
    import json
    claude_json = Path.home() / ".claude.json"
    try:
        if claude_json.exists():
            data = json.loads(claude_json.read_text(encoding="utf-8"))
        else:
            data = {}

        projects = data.setdefault("projects", {})
        # Claude Code uses forward-slash paths as keys
        key = str(project_dir).replace("\\", "/")
        entry = projects.setdefault(key, {})
        if entry.get("hasTrustDialogAccepted"):
            return  # already trusted
        entry["hasTrustDialogAccepted"] = True
        claude_json.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"  Pre-trusted: {key}")
    except Exception as e:
        print(f"  [WARN] Could not pre-trust directory: {e}")
        print(f"  You may need to accept the trust dialog manually in each window.")


def setup_claude_md(project_dir: Path, num_nodes: int = 3, tier: str = "full",
                    lead_model: str | None = None):
    """Create or update CLAUDE.md with collaboration instructions.
    Saves a backup of the original content so cleanup can restore it.

    `lead_model` is used only when `tier == "lite"` — if the lead is a full-tier
    model in an otherwise lite session, the rich lead playbook is appended so the
    lead has the management instructions Haiku/Sonnet devs don't need.
    """
    claude_md = project_dir / "CLAUDE.md"
    backup = STATE_DIR / _BACKUP_NAME

    if tier == "lite":
        with_lead = lead_model is not None and _detect_tier(lead_model) == "full"
        section = collab_md_section_lite(num_nodes, with_lead_playbook=with_lead)
    else:
        section = collab_md_section(num_nodes)

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"{re.escape(COLLAB_MARKER)}.*?{re.escape(COLLAB_MARKER)}",
            re.DOTALL,
        )
        # Save backup of original (pre-collab) content — only if we don't have one yet
        if not backup.exists():
            original = pattern.sub("", content).strip() if pattern.search(content) else content
            backup.write_text(original, encoding="utf-8")
        if pattern.search(content):
            content = pattern.sub(section, content)
        else:
            content = content.rstrip() + "\n\n" + section
    else:
        content = section
        # No existing file — backup is empty (file was created by us)
        if not backup.exists():
            backup.write_text("", encoding="utf-8")

    claude_md.write_text(content, encoding="utf-8")
    print(f"  Updated: {claude_md}")


def cleanup_claude_md(project_dir: Path):
    """Remove collaboration instructions from CLAUDE.md, restoring original content.
    Uses the backup saved during setup, or strips markers if no backup exists."""
    claude_md = project_dir / "CLAUDE.md"
    backup = STATE_DIR / _BACKUP_NAME

    if not claude_md.exists():
        print("  No CLAUDE.md to clean up.")
        return

    if backup.exists():
        original = backup.read_text(encoding="utf-8")
        if original:
            claude_md.write_text(original, encoding="utf-8")
            print(f"  Restored: {claude_md} (from backup)")
        else:
            # Backup was empty — the file was created by us, so remove it
            claude_md.unlink()
            print(f"  Removed: {claude_md} (was created by collab session)")
        backup.unlink()
    else:
        # No backup — strip markers manually
        content = claude_md.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"\n*{re.escape(COLLAB_MARKER)}.*?{re.escape(COLLAB_MARKER)}\n*",
            re.DOTALL,
        )
        cleaned = pattern.sub("", content).strip()
        if cleaned:
            claude_md.write_text(cleaned + "\n", encoding="utf-8")
            print(f"  Cleaned: {claude_md} (stripped collab section)")
        else:
            claude_md.unlink()
            print(f"  Removed: {claude_md} (only contained collab section)")


def reset_state():
    """Clear previous collaboration state."""
    result = subprocess.run(
        [sys.executable, str(COLLAB_PY), "reset", "--confirm"],
        capture_output=True, text=True,
    )
    print(f"  {result.stdout.strip()}")


# Named WT window \u2014 every collab tab is added to this window so the
# session is one window with N tabs instead of N separate windows.
WT_WINDOW_NAME = "collab"


def _launch_windows(project_dir: Path, role_name: str, model: str):
    """Launch one Claude Code instance in a Windows console."""
    bat = STATE_DIR / f"_run_{role_name}.bat"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    colors = get_role_color(role_name)
    esc = "\x1b"  # literal ESC for ANSI sequences in batch
    label = role_name.upper().replace("DEV", "DEV ")
    bar = "\u2550" * 48

    bat.write_text(
        f'@echo off\r\n'
        f'title Collab: {role_name}\r\n'
        # Set Windows Terminal tab color (silently ignored by plain cmd)
        f'echo {esc}]9;4;3;{colors["tab"]}{esc}\\\r\n'
        # Print colored role banner
        f'echo.\r\n'
        f'echo {esc}{colors["fg"]}{bar}{esc}[0m\r\n'
        f'echo {esc}{colors["fg"]}  {colors["icon"]}  {label}  {colors["icon"]}  ({model}){esc}[0m\r\n'
        f'echo {esc}{colors["fg"]}{bar}{esc}[0m\r\n'
        f'echo.\r\n'
        f'set "COLLAB_ROLE={role_name}"\r\n'
        f'cd /d "{project_dir}"\r\n'
        f'claude --model {model}{" --dangerously-skip-permissions" if SKIP_PERMISSIONS else ""}\r\n',
        encoding="utf-8",
    )
    # Try Windows Terminal with a named window so all tabs land together.
    # `wt -w <name> nt` opens (or focuses) that named window and adds a tab to it.
    tab_title = role_name.upper().replace("DEV", "DEV ")
    tab_color = colors["tab"]
    wt_available = shutil.which("wt") is not None
    if wt_available:
        subprocess.Popen(
            f'wt -w {WT_WINDOW_NAME} nt --title "{tab_title}" --tabColor "{tab_color}" cmd /k "{bat}"',
            shell=True,
        )
    else:
        subprocess.Popen(
            f'start "Collab: {role_name}" cmd /k "{bat}"',
            shell=True,
        )


def _launch_unix_tmux(project_dir: Path, role_name: str, model: str, session_name: str = "collab"):
    """Launch one Claude Code instance in a tmux window."""
    sh = STATE_DIR / f"_run_{role_name}.sh"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    colors = get_role_color(role_name)
    label = role_name.upper().replace("DEV", "DEV ")
    bar = "\u2550" * 48
    claude_flags = f"--model {model}"
    if SKIP_PERMISSIONS:
        claude_flags += " --dangerously-skip-permissions"

    sh.write_text(
        f'#!/usr/bin/env bash\n'
        f'# Auto-generated by launcher.py for {role_name}\n'
        f'export COLLAB_ROLE="{role_name}"\n'
        f'cd "{project_dir}"\n'
        f'echo\n'
        f'echo -e "\\033{colors["fg"]}{bar}\\033[0m"\n'
        f'echo -e "\\033{colors["fg"]}  {colors["icon"]}  {label}  {colors["icon"]}  ({model})\\033[0m"\n'
        f'echo -e "\\033{colors["fg"]}{bar}\\033[0m"\n'
        f'echo\n'
        f'claude {claude_flags}\n',
        encoding="utf-8",
    )
    os.chmod(str(sh), 0o755)

    # Check if tmux session exists; create or add window
    check = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    window_name = f"collab_{role_name}"
    if check.returncode != 0:
        # Create new session with this as the first window
        subprocess.Popen(
            ["tmux", "new-session", "-d", "-s", session_name,
             "-n", window_name, str(sh)],
        )
    else:
        # Add a new window to the existing session
        subprocess.Popen(
            ["tmux", "new-window", "-t", session_name,
             "-n", window_name, str(sh)],
        )


def _launch_unix_terminal(project_dir: Path, role_name: str, model: str):
    """Launch one Claude Code instance in a new terminal emulator window (no tmux)."""
    sh = STATE_DIR / f"_run_{role_name}.sh"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    colors = get_role_color(role_name)
    label = role_name.upper().replace("DEV", "DEV ")
    bar = "\u2550" * 48
    claude_flags = f"--model {model}"
    if SKIP_PERMISSIONS:
        claude_flags += " --dangerously-skip-permissions"

    sh.write_text(
        f'#!/usr/bin/env bash\n'
        f'export COLLAB_ROLE="{role_name}"\n'
        f'cd "{project_dir}"\n'
        f'echo\n'
        f'echo -e "\\033{colors["fg"]}{bar}\\033[0m"\n'
        f'echo -e "\\033{colors["fg"]}  {colors["icon"]}  {label}  {colors["icon"]}  ({model})\\033[0m"\n'
        f'echo -e "\\033{colors["fg"]}{bar}\\033[0m"\n'
        f'echo\n'
        f'claude {claude_flags}\n',
        encoding="utf-8",
    )
    os.chmod(str(sh), 0o755)

    if sys.platform == "darwin":
        # macOS: use osascript to open Terminal.app
        apple_script = (
            f'tell application "Terminal"\n'
            f'  activate\n'
            f'  do script "{sh}"\n'
            f'end tell'
        )
        subprocess.Popen(["osascript", "-e", apple_script])
    else:
        # Linux: try common terminal emulators
        for term_cmd in [
            ["gnome-terminal", "--title", f"Collab: {label}", "--", str(sh)],
            ["konsole", "--new-tab", "-e", str(sh)],
            ["xfce4-terminal", "--title", f"Collab: {label}", "-e", str(sh)],
            ["xterm", "-title", f"Collab: {label}", "-e", str(sh)],
        ]:
            if shutil.which(term_cmd[0]):
                subprocess.Popen(term_cmd)
                return
        print(f"  [WARN] No terminal emulator found for {role_name}.")
        print(f"         Run manually: {sh}")


def inject_startup(role_models: dict, lead_prompt: str | None,
                   pid_timeout: float = 20.0, settle_after_pid: float = 6.0):
    """After tabs are open, inject `/effort max` (where supported) into each tab,
    and the user's prompt into lead. Best-effort — prints fallback if injection fails.

    role_models: {"lead": "opus", "dev1": "sonnet", ...}
    lead_prompt: text to send to lead after /effort max, or None to skip.
    """
    try:
        from inject import get_backend
    except Exception as e:
        print(f"  [WARN] Could not import injection backend: {e}")
        print(f"  Type `/effort max` in each window manually.")
        return

    backend = get_backend()
    if backend is None:
        print("  [WARN] No injection backend available. Type `/effort max` manually in each window.")
        return

    # Poll for all roles' PIDs to appear
    needed = set(role_models.keys())
    deadline = time.time() + pid_timeout
    found = {}
    while time.time() < deadline and needed:
        sessions = backend.list_sessions()
        for role in list(needed):
            if role in sessions:
                found[role] = sessions[role]
                needed.remove(role)
        if needed:
            time.sleep(0.5)

    if needed:
        print(f"  [WARN] Couldn't find sessions for: {', '.join(sorted(needed))}")
        print(f"  Type `/effort max` in those windows manually.")

    if not found:
        return

    # Let Claude Code finish booting and reach the prompt
    print(f"  Waiting {settle_after_pid:.0f}s for Claude Code to reach input prompt...")
    time.sleep(settle_after_pid)

    # Inject /effort max where the model supports it
    for role in sorted(found.keys(), key=lambda r: (r != "lead", r)):
        model = role_models.get(role, "")
        if _supports_effort(model):
            ok = backend.inject(role, "/effort max")
            print(f"    {role:<8} /effort max -> {'OK' if ok else 'FAILED'}")
            time.sleep(0.4)
        else:
            print(f"    {role:<8} skipped /effort (Haiku doesn't support it)")

    # Inject lead's prompt
    if lead_prompt and "lead" in found:
        time.sleep(0.6)
        ok = backend.inject("lead", lead_prompt)
        if ok:
            print(f"    lead     prompt injected ({len(lead_prompt)} chars)")
        else:
            print(f"    lead     [WARN] prompt injection failed — paste manually:")
            print(f"             {lead_prompt}")


def launch_instance(project_dir: Path, role_name: str, model: str):
    """Launch one Claude Code instance — auto-detects platform."""
    if sys.platform == "win32":
        _launch_windows(project_dir, role_name, model)
    elif shutil.which("tmux"):
        _launch_unix_tmux(project_dir, role_name, model)
    else:
        _launch_unix_terminal(project_dir, role_name, model)


def _read_session_state() -> dict:
    """Read existing nodes.json to discover the previous session's nodes."""
    nodes_file = STATE_DIR / "nodes.json"
    if not nodes_file.exists():
        return {}
    try:
        import json
        return json.loads(nodes_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_resume_summary() -> str:
    """Build a text summary of session state for the resume prompt."""
    import json
    lines = []
    tasks_file = STATE_DIR / "tasks.json"
    nodes_file = STATE_DIR / "nodes.json"

    if nodes_file.exists():
        try:
            nodes = json.loads(nodes_file.read_text(encoding="utf-8"))
            names = list(nodes.keys())
            lines.append(f"Previous nodes: {', '.join(names)}")
            for n, info in nodes.items():
                working = info.get("working_on", "")
                status = info.get("status", "?")
                if working:
                    lines.append(f"  {n} was: {status}, working on: {working}")
        except Exception:
            pass

    if tasks_file.exists():
        try:
            tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
            active = [(tid, t) for tid, t in tasks.items()
                      if t["status"] in ("active", "claimed", "open")]
            done = [t for t in tasks.values() if t["status"] == "done"]
            lines.append(f"Tasks: {len(done)} done, {len(active)} in progress/open")
            for tid, t in active:
                assignee = t.get("assigned_to", "unassigned")
                lines.append(f"  #{tid} [{t['status']}] {t['title']} (-> {assignee})")
        except Exception:
            pass

    return "\n".join(lines) if lines else "No previous state found."


def resume_session(project_dir: Path, tier: str):
    """Resume a previous collaboration session — re-launch terminals without resetting state."""
    nodes = _read_session_state()
    if not nodes:
        print("  [ERROR] No previous session found (state/nodes.json missing or empty).")
        print("  Use a normal launch instead: python launcher.py <project_dir>")
        sys.exit(1)

    num_nodes = len(nodes)
    role_names = sorted(nodes.keys(), key=lambda n: (n != "lead", n))

    print(f"  Resuming session with {num_nodes} node(s): {', '.join(role_names)}")
    print()

    summary = _build_resume_summary()
    print(f"  Session state:\n")
    for line in summary.splitlines():
        print(f"    {line}")
    print()

    # Re-inject CLAUDE.md (state is preserved, just refresh instructions)
    pre_trust_directory(project_dir)
    print("  Refreshing CLAUDE.md...")
    setup_claude_md(project_dir, num_nodes, tier)

    # Re-launch terminals
    print(f"\n  Re-launching {num_nodes} instance(s)...\n")
    for i, role_name in enumerate(role_names):
        launch_instance(project_dir, role_name, CLAUDE_MODEL)
        desc = nodes[role_name].get("role", "")
        print(f"    {role_name:<8} {desc}")
        if i < num_nodes - 1:
            time.sleep(2)

    using_tmux = sys.platform != "win32" and shutil.which("tmux")
    print(f"""
  ==========================================
     {num_nodes} instance(s) resumed!
  ==========================================

  State was PRESERVED — all tasks, messages, and context intact.

  In EACH window, tell the instance to resume:

      Resume the collaboration session. Run `echo $COLLAB_ROLE` to
      find your role, join, poll for state, and pick up where you
      left off. Check your assigned tasks and continue working.

  Session state:
    {summary.replace(chr(10), chr(10) + '    ')}
""")


# ══════════════════════════════════════════════════════════════
#  Interactive Wizard
# ══════════════════════════════════════════════════════════════

def _ask(prompt: str, default: str | None = None) -> str:
    """Prompt the user; show default in brackets; return stripped input or default."""
    suffix = f" [{default}]" if default else ""
    raw = input(f"  {prompt}{suffix}: ").strip()
    return raw if raw else (default or "")


def _ask_int(prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        raw = _ask(prompt, str(default))
        try:
            n = int(raw)
            if n < minimum:
                print(f"    Must be >= {minimum}")
                continue
            return n
        except ValueError:
            print(f"    Not a number: {raw!r}")


def _pick_model(prompt: str, default_id: str = "opus") -> str:
    """Show MODEL_MENU + custom option, return chosen model ID."""
    print(f"\n  {prompt}")
    for i, (mid, label) in enumerate(MODEL_MENU, 1):
        marker = " *" if mid == default_id else "  "
        print(f"   {marker}{i}) {mid:<28} {label}")
    print(f"     {len(MODEL_MENU)+1}) custom...                    type any model ID")

    while True:
        raw = input(f"\n    pick [default {default_id}]: ").strip()
        if not raw:
            return default_id
        try:
            n = int(raw)
            if 1 <= n <= len(MODEL_MENU):
                return MODEL_MENU[n - 1][0]
            if n == len(MODEL_MENU) + 1:
                custom = input("    enter model ID: ").strip()
                if custom:
                    return custom
                continue
        except ValueError:
            pass
        # Allow typing a model ID directly
        if raw:
            return raw


def run_wizard(initial_project_dir: Path | None = None):
    """Interactive launcher. Collects model + role choices, then launches."""
    print()
    print("=" * 52)
    print("   ccollab — interactive launch wizard")
    print("=" * 52)
    print()

    # 0. Project directory
    default_proj = str(initial_project_dir or Path.cwd())
    proj_raw = _ask("Project directory", default_proj).strip('"').strip("'")
    project_dir = Path(proj_raw).resolve()
    if not project_dir.is_dir():
        print(f"  [ERROR] Not a directory: {project_dir}")
        sys.exit(1)

    # 1 + 2. Lead and subordinate models
    lead_model = _pick_model("Lead model", default_id="opus")
    dev_model  = _pick_model("Subordinate model (applied to all devs)", default_id=lead_model)

    # 3. Number of subordinates (so total nodes = devs + 1)
    print()
    num_devs = _ask_int("How many subordinates?", default=2, minimum=1)
    num_nodes = num_devs + 1

    # 4. Lead role (free text)
    print()
    lead_role = _ask("Lead role (free text)",
                     default="Coordination, architecture, and task management")

    # 5. Per-dev roles (free text)
    print()
    dev_roles = []
    for i in range(1, num_devs + 1):
        suggested = "Primary implementation" if i == 1 else \
                    "Review and testing"     if i == 2 else \
                    f"Development node {i}"
        role = _ask(f"Role for dev{i}", default=suggested)
        dev_roles.append(role)

    # 6. Lead prompt
    print()
    print("  Lead prompt — what should the session start working on?")
    print("  (Single line. Press Enter on an empty prompt to skip auto-injection.)")
    lead_prompt = input("  > ").strip()

    # ── Confirm ──
    print()
    print("  -- Launch summary --")
    print(f"    project   : {project_dir}")
    print(f"    lead      : {lead_model}    role: {lead_role}")
    print(f"    devs ({num_devs}) : {dev_model}")
    for i, r in enumerate(dev_roles, 1):
        print(f"      dev{i}    role: {r}")
    if lead_prompt:
        preview = lead_prompt if len(lead_prompt) < 70 else lead_prompt[:67] + "..."
        print(f"    prompt    : {preview}")
    else:
        print(f"    prompt    : (none — type manually in lead window)")
    print()
    confirm = _ask("Proceed? [Y/n]", "y").lower()
    if confirm not in ("", "y", "yes"):
        print("  Aborted.")
        sys.exit(0)

    # ── Launch ──
    role_models = {"lead": lead_model}
    for i in range(1, num_devs + 1):
        role_models[f"dev{i}"] = dev_model

    role_descs = [("lead", lead_role)] + [(f"dev{i+1}", dev_roles[i]) for i in range(num_devs)]

    tier = _session_tier(lead_model, dev_model)
    print(f"\n  Tier:    {tier} ({'simplified protocol' if tier == 'lite' else 'full protocol'})")
    if tier == "lite" and _detect_tier(lead_model) == "full":
        print("           (lead playbook appended for Opus lead in mixed session)")

    print("\n  Resetting collaboration state...")
    reset_state()

    pre_trust_directory(project_dir)
    print("  Configuring CLAUDE.md...")
    setup_claude_md(project_dir, num_nodes, tier, lead_model=lead_model)

    print(f"\n  Launching {num_nodes} instance(s) into one tabbed window...\n")
    for i, (name, desc) in enumerate(role_descs):
        launch_instance(project_dir, name, role_models[name])
        print(f"    {name:<8} ({role_models[name]})  {desc}")
        if i < len(role_descs) - 1:
            time.sleep(2)

    print(f"\n  All tabs requested. Polling for sessions to inject startup...")
    inject_startup(role_models, lead_prompt or None)

    print(f"""
  ==========================================
     Session is live ({num_nodes} tabs in window "{WT_WINDOW_NAME}")
  ==========================================

  State dir: {STATE_DIR}
  Stop:      ccollab --stop
  Resume:    ccollab --resume
""")


def main():
    import argparse as _ap
    parser = _ap.ArgumentParser(
        description="Launch N Claude Code instances for real-time collaboration.",
        formatter_class=_ap.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_dir", nargs="?", default=None,
                        help="Path to the project directory")
    parser.add_argument("-n", "--nodes", type=int, default=3,
                        help="Number of collaboration nodes (default: 3, min: 2)")
    parser.add_argument("--tier", choices=["auto", "full", "lite"], default=None,
                        help="Protocol tier: full (Opus), lite (Haiku/Sonnet), auto (detect from model)")
    parser.add_argument("--stop", action="store_true",
                        help="Clean up: remove collab instructions from CLAUDE.md and reset state")
    parser.add_argument("--resume", action="store_true",
                        help="Resume a previous session — re-launch terminals, preserve state")
    parser.add_argument("--no-wizard", action="store_true",
                        help="Skip the interactive wizard even when no flags are given (use legacy flow)")
    args = parser.parse_args()

    # ── Wizard mode: default when invoked with no flags / no nodes override ──
    invoked_bare = (
        not args.stop and not args.resume and not args.no_wizard
        and "--nodes" not in sys.argv and "-n" not in sys.argv
        and args.tier is None
    )
    if invoked_bare:
        initial = Path(args.project_dir).resolve() if args.project_dir else None
        run_wizard(initial)
        return

    # ── Resume mode: re-launch without resetting ──
    if args.resume:
        project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd()
        if not project_dir.is_dir():
            print(f"  [ERROR] Not a directory: {project_dir}")
            sys.exit(1)
        tier = args.tier or COLLAB_TIER
        if tier == "auto":
            tier = _detect_tier(CLAUDE_MODEL)
        print()
        print("=" * 52)
        print("   Claude Code Collaboration — RESUME")
        print("=" * 52)
        print()
        resume_session(project_dir, tier)
        return

    # ── Stop mode: clean up and exit ──
    if args.stop:
        project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd()
        print("\n  Cleaning up collaboration session...\n")
        cleanup_claude_md(project_dir)
        reset_state()
        print("\n  Session cleaned up. CLAUDE.md restored to pre-session state.\n")
        return

    num_nodes = max(2, args.nodes)

    print()
    print("=" * 52)
    print("   Claude Code Collaboration Launcher")
    print("=" * 52)
    print()

    # ── Get project directory ──
    if args.project_dir:
        project_dir = Path(args.project_dir).resolve()
    else:
        raw = input("  Project directory: ").strip().strip('"').strip("'")
        if not raw:
            print("  [ERROR] No directory provided.")
            sys.exit(1)
        project_dir = Path(raw).resolve()

    if not project_dir.is_dir():
        print(f"  [ERROR] Not a directory: {project_dir}")
        sys.exit(1)

    # Determine tier
    tier = args.tier or COLLAB_TIER
    if tier == "auto":
        tier = _detect_tier(CLAUDE_MODEL)

    print(f"  Project: {project_dir}")
    print(f"  Nodes:   {num_nodes}")
    print(f"  Tier:    {tier} ({'simplified protocol' if tier == 'lite' else 'full protocol'})\n")

    roles = build_roles(num_nodes)

    # ── Step 1: Reset collab state ──
    print("  Resetting collaboration state...")
    reset_state()

    # ── Step 2: Pre-trust directory + set up CLAUDE.md ──
    pre_trust_directory(project_dir)
    print("  Configuring CLAUDE.md...")
    setup_claude_md(project_dir, num_nodes, tier)

    # ── Step 3: Launch N instances ──
    print(f"\n  Launching {num_nodes} instances...\n")
    for i, (name, desc) in enumerate(roles):
        launch_instance(project_dir, name, CLAUDE_MODEL)
        print(f"    {name:<8} {desc}")
        if i < len(roles) - 1:
            time.sleep(2)  # Stagger to avoid lock contention on startup

    # Build roles summary
    role_lines = "\n".join(f"    {name:<8} - {desc}" for name, desc in roles)

    # Detect launch mode for user instructions
    using_tmux = sys.platform != "win32" and shutil.which("tmux")
    attach_hint = ""
    if using_tmux:
        attach_hint = "\n  To view all instances:  tmux attach -t collab\n  Switch windows:         Ctrl-B then N (next) / P (prev)\n"

    print(f"""
  ==========================================
     {num_nodes} Claude Code instances launched!
  ==========================================

  Platform: {"Windows" if sys.platform == "win32" else "Unix"} | {"tmux" if using_tmux else "Windows Terminal" if sys.platform == "win32" else "terminal emulator"}
  Settings: {CLAUDE_MODEL}{' | --dangerously-skip-permissions' if SKIP_PERMISSIONS else ''}
{attach_hint}
  In EACH window, type:

      /effort max

  Then describe the project task or just say "go".
  Each instance will auto-join the collaboration and
  begin coordinating with the others.

  Roles:
{role_lines}

  State dir: {STATE_DIR}
""")


if __name__ == "__main__":
    main()
