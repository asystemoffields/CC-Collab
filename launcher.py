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


CLAUDE_MODEL = os.environ.get("COLLAB_MODEL", "claude-opus-4-6")
SKIP_PERMISSIONS = os.environ.get("COLLAB_SKIP_PERMISSIONS", "1") == "1"

# Model capability tier: "full" for Opus, "lite" for Haiku/Sonnet
# "lite" simplifies CLAUDE.md instructions, adds explicit step-by-step guidance,
# reduces command surface, and adds error recovery hints
COLLAB_TIER = os.environ.get("COLLAB_TIER", "auto")  # auto|full|lite

COLLAB_MARKER = "<!-- COLLAB:AUTO -->"


def _detect_tier(model: str) -> str:
    """Detect capability tier from model name."""
    model_lower = model.lower()
    if "opus" in model_lower:
        return "full"
    # Sonnet and Haiku get the simplified protocol
    return "lite"


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
    return f"""{COLLAB_MARKER}
## Multi-Instance Collaboration

You are part of a **{num_nodes}-node collaboration session**. Other Claude Code instances are working on this project at the same time. You MUST coordinate with them.

**CRITICAL: Execute the startup steps below BEFORE doing any other work.** The moment you receive a user message, run these first, then respond to their request.

### Startup (MANDATORY — run immediately on first user message)

1. Detect your assigned role:
   ```bash
   echo $COLLAB_ROLE
   ```

2. Join the collaboration using that value:
{join_block}

3. Check current state and pending work:
   ```bash
   python "{COLLAB_PY_BASH}" status
   python "{COLLAB_PY_BASH}" poll <your-name>
   ```

4. If you are `lead`: create tasks, assign them to {dev_list}, and manage the session (see **Lead Playbook** below).
   If you are a dev node ({dev_list}): check for assigned tasks. If none, take initiative (see **Dev Playbook** below).

### Lead Playbook (lead instance only)

As lead, you are the **autonomous manager** of this session. You can operate without human intervention.

**Your responsibilities:**
- Break the user's request into discrete tasks and assign them to {dev_list}
- Do your own implementation work (architecture, shared files, complex pieces)
- Monitor progress and unblock devs when they're stuck or idle

**Managing dev instances — you have direct terminal control:**
- `windows` — verify dev consoles are detected before using inject/interrupt
- `nudge <target> "<msg>"` — **safest option**. Creates a signal file and injects a `poll` command into their terminal. Use this when you've assigned new tasks or sent messages and need them to notice.
- `inject <target> "<prompt>"` — types text + Enter directly into their terminal, as if a human typed it. Use this to give them direct instructions when messaging isn't enough.
- `interrupt <target>` — sends Escape twice to stop their current generation. Use this if they're going in the wrong direction or you need their attention immediately.

**Typical autonomous workflow:**
1. Create and assign tasks → `task add "..." --assign dev1 --by lead`
2. Nudge both devs to pick up work → `nudge dev1 "New tasks assigned"` and `nudge dev2 "New tasks assigned"`
3. Do your own work while they work in parallel
4. Poll periodically to check their progress → `poll lead`
5. When a dev finishes, assign follow-up work and nudge them
6. If a dev seems stuck (no task updates for a while), nudge or send a message
7. If a dev is doing something wrong, interrupt first, wait a moment, then inject corrective instructions

**Important patterns:**
- Always `interrupt` before `inject` if the target is mid-generation — injecting while they're outputting will interleave your text with theirs
- After interrupting, wait ~2 seconds before injecting so the instance settles
- `nudge` is preferred over raw `inject` for routine check-ins — it's lighter and less disruptive
- If devs aren't responding to nudges, they may need a direct `inject` with explicit instructions
- You can chain: `interrupt dev1` → pause → `inject dev1 "Stop current work. Poll for new instructions."` → then send them a message explaining the change

### Dev Playbook (dev instances)

You are an **autonomous developer**, not a passive worker. Take initiative when idle.

**Priority order — what to do next:**
1. Follow any explicit instruction from lead (lead overrides self-direction)
2. Work on tasks assigned to you (`poll` shows your tasks)
3. Claim open unassigned tasks from the board (`task list --status open`)
4. Look at the project yourself — identify work, create tasks, claim them, and start

**After finishing a task:**
1. Mark it done with a result summary
2. Poll for new assignments
3. If nothing assigned, check `task list --status open` for unclaimed work and claim one
4. If no open tasks exist, analyze the project — find bugs, missing tests, improvements. Create + claim your own tasks
5. Broadcast what you're picking up so others don't duplicate

**Proactive behaviors (do these without being asked):**
- See a broken test or bug while working? Fix it or create a task
- Finished early? Run the test suite, review completed work, improve coverage
- Have context another instance needs? Share it via `context set`
- Idle with truly nothing to do? Read the codebase, check for TODOs, look for gaps

**Guardrails:**
- Check `status` and `task list` before starting self-directed work — don't duplicate effort
- Don't make architectural decisions alone — propose via broadcast, let lead decide
- Lock files before editing — if a file is locked by someone else, work on something else
- When in doubt about scope or direction, ask lead via `send`
- If lead tells you to stop or change direction, do it immediately

### Ongoing Protocol (follow throughout the entire session)

**After every file write or task completion**, run:
```bash
python "{COLLAB_PY_BASH}" pending <your-name>   # fast check — if signals found, immediately run:
python "{COLLAB_PY_BASH}" poll <your-name>       # full update
```

**Before/after editing any file:**
```bash
python "{COLLAB_PY_BASH}" lock <your-name> "<file>"    # BEFORE editing
python "{COLLAB_PY_BASH}" unlock <your-name> "<file>"  # AFTER editing
# Never edit a locked file — check first: python "{COLLAB_PY_BASH}" locks
```

**When starting/finishing tasks:**
```bash
python "{COLLAB_PY_BASH}" task update <id> active --by <your-name>
python "{COLLAB_PY_BASH}" task update <id> done --result "<summary>" --by <your-name>
```

### Error Recovery

If a command fails, follow these steps before asking for help:

| Error | Recovery |
|---|---|
| `Node "X" not found` | Run `join` again with your role |
| `Lock timeout` | Wait 5s, retry. If persistent, check `locks` — holder may be stuck |
| `File locked by "Y"` | Do other work first. `send <you> Y "need <file>"` |
| Poll shows no updates | Normal — continue your current task |
| `python` not found | Use `python3` in all commands |
| JSON parse error | State may be corrupted — ask lead to run `validate` |

### Command Reference

**Aliases:** s=status, p=poll, pd=pending, b=broadcast, t=task, c=context, h=health, w=windows, n=nudge

**Every interaction** (use constantly):
```
python "{COLLAB_PY_BASH}" poll <you>                              # Get all updates (alias: p)
python "{COLLAB_PY_BASH}" pending <you>                           # Quick signal check (alias: pd)
python "{COLLAB_PY_BASH}" task update <id> <status> --by <you>    # active|done|blocked + --result "..."
python "{COLLAB_PY_BASH}" lock <you> "<file>"                     # Before editing
python "{COLLAB_PY_BASH}" unlock <you> "<file>"                   # After editing
```

**Communication:**
```
python "{COLLAB_PY_BASH}" send <you> <them> "<msg>"              # Direct message
python "{COLLAB_PY_BASH}" broadcast <you> "<msg>"                 # Message all (alias: b)
python "{COLLAB_PY_BASH}" inbox <you>                             # Read messages
```

**Tasks & context:**
```
python "{COLLAB_PY_BASH}" task add "<title>" --assign <node> --priority high --by <you>
python "{COLLAB_PY_BASH}" task list                               # All tasks (alias: t list)
python "{COLLAB_PY_BASH}" task show <id>                          # Full details
python "{COLLAB_PY_BASH}" context set "<key>" "<val>" --by <you>  # Share info (alias: c)
```

**Diagnostics (when needed):**
```
python "{COLLAB_PY_BASH}" status                                  # Full overview (alias: s)
python "{COLLAB_PY_BASH}" health                                  # Node health (alias: h)
python "{COLLAB_PY_BASH}" locks                                   # Active file locks
python "{COLLAB_PY_BASH}" windows                                 # Detected consoles (alias: w)
```

**Lead-only (terminal control):**
```
python "{COLLAB_PY_BASH}" nudge <target> "<msg>"                  # Signal + poll inject (alias: n)
python "{COLLAB_PY_BASH}" inject <target> "<prompt>"              # Type into their terminal
python "{COLLAB_PY_BASH}" interrupt <target>                      # Send Escape (stop generation)
```

Apply maximum effort and thoroughness to all work in this session.

Full protocol reference: {str(COLLAB_DIR / 'PROTOCOL.md').replace(chr(92), '/')}
{COLLAB_MARKER}"""


def collab_md_section_lite(num_nodes: int = 3) -> str:
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

    return f"""{COLLAB_MARKER}
## Multi-Instance Collaboration (Simplified Protocol)

You are one of {num_nodes} Claude Code instances working together. Follow these rules exactly.

### Step 1: Startup (DO THIS FIRST)

Run these 3 commands before anything else:

```bash
echo $COLLAB_ROLE
```

Then join based on your role:
{join_block}

Then check for work:
```bash
python "{p}" --brief poll <your-name>
```

### Step 2: Your Workflow Loop

Repeat this cycle for every task:

1. **Check for tasks:** `python "{p}" --brief poll <your-name>`
2. **Lock files before editing:** `python "{p}" lock <your-name> "<file>"`
3. **Do the work** (edit files, write code, etc.)
4. **Unlock when done:** `python "{p}" unlock <your-name> "<file>"`
5. **Mark task done:** `python "{p}" task update <id> done --result "<what you did>" --by <your-name>`
6. **Poll again:** `python "{p}" --brief poll <your-name>`

### Essential Commands (6 total)

```
python "{p}" --brief poll <your-name>                              # CHECK for messages and tasks
python "{p}" lock <your-name> "<file>"                             # LOCK before editing
python "{p}" unlock <your-name> "<file>"                           # UNLOCK after editing
python "{p}" task update <id> done --result "<summary>" --by <you> # MARK task done
python "{p}" task update <id> active --by <your-name>              # MARK task started
python "{p}" broadcast <your-name> "<message>"                     # TELL everyone something
```

### Rules

- **ALWAYS poll after finishing any task** — new work may be waiting
- **ALWAYS lock files before editing, unlock after** — prevents conflicts
- **NEVER edit a file someone else has locked** — check `python "{p}" locks` first
- **If a command fails:** try again once. If it still fails, run `python "{p}" --brief poll <your-name>` and continue with other work
- **Use --brief on poll** to save context window space

### When You Have No Tasks

Don't sit idle. Follow this priority order:

1. **Poll** — you may have tasks you haven't seen yet
2. **Check for unclaimed tasks:** `python "{p}" task list --status open` — claim and start one
3. **Find your own work** — look at the project, find bugs, missing tests, or improvements
4. **Create tasks for yourself:** `python "{p}" task add "<what>" --by <your-name>` then claim it
5. **Broadcast what you're doing** so others don't duplicate your work

**Important:** If lead gives you a direct instruction, follow that instead. Only self-direct when no one has told you what to do.

### If You Are Lead

As lead, you also need to:
- Create tasks: `python "{p}" task add "<title>" --assign <node> --by lead`
- Check progress: `python "{p}" --brief status`
- Nudge idle devs: `python "{p}" nudge <target> "<msg>"`

Full protocol: {str(COLLAB_DIR / 'PROTOCOL.md').replace(chr(92), '/')}
{COLLAB_MARKER}"""


_BACKUP_NAME = "_claude_md_backup"


def setup_claude_md(project_dir: Path, num_nodes: int = 3, tier: str = "full"):
    """Create or update CLAUDE.md with collaboration instructions.
    Saves a backup of the original content so cleanup can restore it."""
    claude_md = project_dir / "CLAUDE.md"
    backup = STATE_DIR / _BACKUP_NAME

    if tier == "lite":
        section = collab_md_section_lite(num_nodes)
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


def _launch_windows(project_dir: Path, role_name: str):
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
        f'echo {esc}{colors["fg"]}  {colors["icon"]}  {label}  {colors["icon"]}{esc}[0m\r\n'
        f'echo {esc}{colors["fg"]}{bar}{esc}[0m\r\n'
        f'echo.\r\n'
        f'set "COLLAB_ROLE={role_name}"\r\n'
        f'cd /d "{project_dir}"\r\n'
        f'claude --model {CLAUDE_MODEL}{" --dangerously-skip-permissions" if SKIP_PERMISSIONS else ""}\r\n',
        encoding="utf-8",
    )
    # Try Windows Terminal (persistent tab titles + colors that survive
    # Claude Code overriding the console title). Fall back to plain `start`.
    tab_title = role_name.upper().replace("DEV", "DEV ")
    tab_color = colors["tab"]
    wt_available = shutil.which("wt") is not None
    if wt_available:
        subprocess.Popen(
            f'wt new-tab --title "{tab_title}" --tabColor "{tab_color}" cmd /k "{bat}"',
            shell=True,
        )
    else:
        subprocess.Popen(
            f'start "Collab: {role_name}" cmd /k "{bat}"',
            shell=True,
        )


def _launch_unix_tmux(project_dir: Path, role_name: str, session_name: str = "collab"):
    """Launch one Claude Code instance in a tmux window."""
    sh = STATE_DIR / f"_run_{role_name}.sh"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    colors = get_role_color(role_name)
    label = role_name.upper().replace("DEV", "DEV ")
    bar = "\u2550" * 48
    claude_flags = f"--model {CLAUDE_MODEL}"
    if SKIP_PERMISSIONS:
        claude_flags += " --dangerously-skip-permissions"

    sh.write_text(
        f'#!/usr/bin/env bash\n'
        f'# Auto-generated by launcher.py for {role_name}\n'
        f'export COLLAB_ROLE="{role_name}"\n'
        f'cd "{project_dir}"\n'
        f'echo\n'
        f'echo -e "\\033{colors["fg"]}{bar}\\033[0m"\n'
        f'echo -e "\\033{colors["fg"]}  {colors["icon"]}  {label}  {colors["icon"]}\\033[0m"\n'
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


def _launch_unix_terminal(project_dir: Path, role_name: str):
    """Launch one Claude Code instance in a new terminal emulator window (no tmux)."""
    sh = STATE_DIR / f"_run_{role_name}.sh"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    colors = get_role_color(role_name)
    label = role_name.upper().replace("DEV", "DEV ")
    bar = "\u2550" * 48
    claude_flags = f"--model {CLAUDE_MODEL}"
    if SKIP_PERMISSIONS:
        claude_flags += " --dangerously-skip-permissions"

    sh.write_text(
        f'#!/usr/bin/env bash\n'
        f'export COLLAB_ROLE="{role_name}"\n'
        f'cd "{project_dir}"\n'
        f'echo\n'
        f'echo -e "\\033{colors["fg"]}{bar}\\033[0m"\n'
        f'echo -e "\\033{colors["fg"]}  {colors["icon"]}  {label}  {colors["icon"]}\\033[0m"\n'
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


def launch_instance(project_dir: Path, role_name: str):
    """Launch one Claude Code instance — auto-detects platform."""
    if sys.platform == "win32":
        _launch_windows(project_dir, role_name)
    elif shutil.which("tmux"):
        _launch_unix_tmux(project_dir, role_name)
    else:
        _launch_unix_terminal(project_dir, role_name)


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
    args = parser.parse_args()

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

    # ── Step 2: Set up CLAUDE.md ──
    print("  Configuring CLAUDE.md...")
    setup_claude_md(project_dir, num_nodes, tier)

    # ── Step 3: Launch N instances ──
    print(f"\n  Launching {num_nodes} instances...\n")
    for i, (name, desc) in enumerate(roles):
        launch_instance(project_dir, name)
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
