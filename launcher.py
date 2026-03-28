#!/usr/bin/env python3
"""
Claude Code Collaboration Launcher
===================================
Launches 3 pre-configured Claude Code instances for real-time collaboration.

Usage:
    Double-click launch.bat, or:
    python launcher.py [project_directory]
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

COLLAB_DIR = Path(__file__).parent.resolve()
COLLAB_PY  = COLLAB_DIR / "collab.py"
STATE_DIR  = COLLAB_DIR / "state"

# Bash-friendly path (forward slashes work in Git Bash on Windows)
COLLAB_PY_BASH = str(COLLAB_PY).replace("\\", "/")

ROLES = [
    ("lead", "Coordination, architecture, and task management"),
    ("dev1",  "Primary implementation and development"),
    ("dev2",  "Secondary development, review, and testing"),
]

# ANSI color codes and Windows Terminal tab colors per role
ROLE_COLORS = {
    "lead": {"fg": "[1;33m", "tab": "#E5A00D", "icon": "***"},  # yellow/gold
    "dev1": {"fg": "[1;36m", "tab": "#00B4D8", "icon": "***"},  # cyan
    "dev2": {"fg": "[1;32m", "tab": "#2DC653", "icon": "***"},  # green
}

CLAUDE_MODEL = os.environ.get("COLLAB_MODEL", "claude-opus-4-6")
SKIP_PERMISSIONS = os.environ.get("COLLAB_SKIP_PERMISSIONS", "1") == "1"

COLLAB_MARKER = "<!-- COLLAB:AUTO -->"


def collab_md_section() -> str:
    """Generate the CLAUDE.md block that teaches each instance how to collaborate."""
    return f"""{COLLAB_MARKER}
## Multi-Instance Collaboration

You are part of a **3-node collaboration session**. Other Claude Code instances are working on this project at the same time. You MUST coordinate with them.

**CRITICAL: Execute the startup steps below BEFORE doing any other work.** The moment you receive a user message, run these first, then respond to their request.

### Startup (MANDATORY — run immediately on first user message)

1. Detect your assigned role:
   ```bash
   echo $COLLAB_ROLE
   ```

2. Join the collaboration using that value:
   - If `lead`: `python "{COLLAB_PY_BASH}" join lead --role "Coordination, architecture, task management"`
   - If `dev1`: `python "{COLLAB_PY_BASH}" join dev1 --role "Primary implementation"`
   - If `dev2`: `python "{COLLAB_PY_BASH}" join dev2 --role "Review, testing, secondary development"`

3. Check current state and pending work:
   ```bash
   python "{COLLAB_PY_BASH}" status
   python "{COLLAB_PY_BASH}" poll <your-name>
   ```

4. If you are `lead`: create tasks, assign them to `dev1` and `dev2`, and manage the session (see **Lead Playbook** below).
   If you are `dev1` or `dev2`: check for assigned tasks and start working.

### Lead Playbook (lead instance only)

As lead, you are the **autonomous manager** of this session. You can operate without human intervention.

**Your responsibilities:**
- Break the user's request into discrete tasks and assign them to `dev1` and `dev2`
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

### Ongoing Protocol (follow throughout the entire session)

- **Check for signals after every file write or major action**: `python "{COLLAB_PY_BASH}" pending <your-name>` — if it shows signals or pending items, immediately run `poll`
- **Poll after completing each task**: `python "{COLLAB_PY_BASH}" poll <your-name>` — don't wait for 3-5 interactions, poll as soon as you finish writing a file or completing a task
- **Lock before editing**: `python "{COLLAB_PY_BASH}" lock <your-name> "<file>"`
- **Unlock after editing**: `python "{COLLAB_PY_BASH}" unlock <your-name> "<file>"`
- **Share discoveries**: `python "{COLLAB_PY_BASH}" context set "<key>" "<value>" --by <your-name>`
- **Announce decisions**: `python "{COLLAB_PY_BASH}" broadcast <your-name> "<message>"`
- **Track task progress**: `python "{COLLAB_PY_BASH}" task update <id> active --by <your-name>` when starting, `python "{COLLAB_PY_BASH}" task update <id> done --result "<summary>" --by <your-name>` when finished
- **Never edit a locked file** — check first: `python "{COLLAB_PY_BASH}" locks`

### Command Reference
```
python "{COLLAB_PY_BASH}" send <you> <them> "<msg>"              # Direct message
python "{COLLAB_PY_BASH}" broadcast <you> "<msg>"                 # Message everyone
python "{COLLAB_PY_BASH}" inbox <you>                             # Check messages
python "{COLLAB_PY_BASH}" context set "<key>" "<val>" --by <you>  # Share persistent info
python "{COLLAB_PY_BASH}" context get                             # View all shared context
python "{COLLAB_PY_BASH}" task add "<title>" --assign <node> --priority high --by <you>
python "{COLLAB_PY_BASH}" task list                               # View all tasks
python "{COLLAB_PY_BASH}" task claim <you> <id>                   # Claim a task
python "{COLLAB_PY_BASH}" task update <id> done --result "<x>" --by <you>
python "{COLLAB_PY_BASH}" request <you> <them> "<desc>"           # Ask for help (task + msg)
python "{COLLAB_PY_BASH}" pending <you>                           # Quick check for signals (fast!)
python "{COLLAB_PY_BASH}" poll <you>                              # Get all updates (full)
python "{COLLAB_PY_BASH}" status                                  # Full overview
python "{COLLAB_PY_BASH}" lock <you> "<file>"                     # Lock file
python "{COLLAB_PY_BASH}" unlock <you> "<file>"                   # Unlock file
python "{COLLAB_PY_BASH}" windows                                 # List all consoles (lead)
python "{COLLAB_PY_BASH}" inject <target> "<prompt>"              # Type into target's terminal (lead)
python "{COLLAB_PY_BASH}" interrupt <target>                      # Send Escape to target (lead)
python "{COLLAB_PY_BASH}" nudge <target> "<msg>"                  # Signal + inject poll (lead)
python "{COLLAB_PY_BASH}" whoami <your-name>                      # Print role banner to identify terminal
```

Apply maximum effort and thoroughness to all work in this session.

Full protocol reference: {str(COLLAB_DIR / 'PROTOCOL.md').replace(chr(92), '/')}
{COLLAB_MARKER}"""


def setup_claude_md(project_dir: Path):
    """Create or update CLAUDE.md with collaboration instructions."""
    claude_md = project_dir / "CLAUDE.md"
    section = collab_md_section()

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        # Replace existing auto-section if present
        pattern = re.compile(
            rf"{re.escape(COLLAB_MARKER)}.*?{re.escape(COLLAB_MARKER)}",
            re.DOTALL,
        )
        if pattern.search(content):
            content = pattern.sub(section, content)
        else:
            content = content.rstrip() + "\n\n" + section
    else:
        content = section

    claude_md.write_text(content, encoding="utf-8")
    print(f"  Updated: {claude_md}")


def reset_state():
    """Clear previous collaboration state."""
    result = subprocess.run(
        [sys.executable, str(COLLAB_PY), "reset", "--confirm"],
        capture_output=True, text=True,
    )
    print(f"  {result.stdout.strip()}")


def launch_instance(project_dir: Path, role_name: str):
    """Launch one Claude Code instance in its own console window."""
    bat = STATE_DIR / f"_run_{role_name}.bat"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    colors = ROLE_COLORS.get(role_name, {"fg": "[1;37m", "tab": "#808080", "icon": "*"})
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
    # start opens a new console; the first quoted arg is the window title
    subprocess.Popen(
        f'start "Collab: {role_name}" cmd /k "{bat}"',
        shell=True,
    )


def main():
    print()
    print("=" * 52)
    print("   Claude Code Collaboration Launcher")
    print("=" * 52)
    print()

    # ── Get project directory ──
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1]).resolve()
    else:
        raw = input("  Project directory: ").strip().strip('"').strip("'")
        if not raw:
            print("  [ERROR] No directory provided.")
            sys.exit(1)
        project_dir = Path(raw).resolve()

    if not project_dir.is_dir():
        print(f"  [ERROR] Not a directory: {project_dir}")
        sys.exit(1)

    print(f"  Project: {project_dir}\n")

    # ── Step 1: Reset collab state ──
    print("  Resetting collaboration state...")
    reset_state()

    # ── Step 2: Set up CLAUDE.md ──
    print("  Configuring CLAUDE.md...")
    setup_claude_md(project_dir)

    # ── Step 3: Launch 3 instances ──
    print("\n  Launching instances...\n")
    for i, (name, desc) in enumerate(ROLES):
        launch_instance(project_dir, name)
        print(f"    {name:<5}  {desc}")
        if i < len(ROLES) - 1:
            time.sleep(2)  # Stagger to avoid lock contention on startup

    print(f"""
  ==========================================
     3 Claude Code instances launched!
  ==========================================

  Settings: {CLAUDE_MODEL}{' | --dangerously-skip-permissions' if SKIP_PERMISSIONS else ''}

  In EACH window, type:

      /effort max

  Then describe the project task or just say "go".
  Each instance will auto-join the collaboration and
  begin coordinating with the others.

  Roles:
    lead  -  Coordination & architecture
    dev1  -  Primary implementation
    dev2  -  Review, testing, secondary dev

  State dir: {STATE_DIR}
""")


if __name__ == "__main__":
    main()
