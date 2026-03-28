# cc-opus-swarm

Multi-instance Claude Code collaboration. Launch 3 Claude Code instances that coordinate in real-time through a shared file-based state system — one leads, two develop, all communicate autonomously.

## How It Works

The launcher opens three terminal windows, each running Claude Code with a designated role (**lead**, **dev1**, **dev2**). A shared `state/` directory holds JSON files for messages, tasks, file locks, and context. Instances coordinate by polling this state — no server, no network, just files.

The **lead** instance acts as an autonomous manager: it breaks down the user's request into tasks, assigns them to dev instances, and can directly control their terminals (inject prompts, interrupt generation, nudge for attention) via Win32 `WriteConsoleInput`.

The launcher auto-injects collaboration instructions into the target project's `CLAUDE.md`, so each instance knows how to participate without manual setup.

## Requirements

- **Python 3.12+** (stdlib only, zero dependencies)
- **Windows** (terminal injection uses Win32 API via ctypes)
- **Claude Code CLI** installed and authenticated

## Quick Start

```bash
git clone https://github.com/asystemoffields/cc-opus-swarm.git
cd cc-opus-swarm

# Option A: double-click
launch.bat

# Option B: command line
python launcher.py "C:\path\to\your\project"
```

Each window will show a color-coded role banner. Type `/effort max` in each, then describe your task to the lead instance (or just say "go" if the project context is self-explanatory). The lead will delegate work to dev1 and dev2 automatically.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `COLLAB_MODEL` | `claude-opus-4-6` | Model ID passed to `claude --model` |
| `COLLAB_SKIP_PERMISSIONS` | `1` | Set to `0` to require permission prompts |
| `COLLAB_STATE_DIR` | `./state` | Directory for collaboration state files |

## Architecture

```
launcher.py          Resets state, writes CLAUDE.md, opens 3 terminal windows
collab.py            CLI tool used by each instance for all coordination
state/
  nodes.json         Active instances and their roles
  messages.json      Direct and broadcast messages
  tasks.json         Task board (open/claimed/active/done/blocked)
  locks.json         File-level exclusive locks
  context.json       Shared key-value store for decisions/config
  log.json           Activity feed
  _signal_<name>     Per-node signal files for fast change detection
```

All state access uses OS-level file locking (`msvcrt` on Windows) to prevent corruption from concurrent writes.

## Command Reference

```
collab.py join <name> --role "<role>"              Register as a node
collab.py status                                   Full session overview
collab.py poll <name>                              Get all updates since last poll
collab.py pending <name>                           Quick signal check (fast)

collab.py send <you> <them> "<msg>"                Direct message
collab.py broadcast <you> "<msg>"                  Message all nodes
collab.py inbox <you>                              Check unread messages

collab.py task add "<title>" --assign <node> --by <you>
collab.py task list                                View all tasks
collab.py task claim <you> <id>                    Claim an open task
collab.py task update <id> done --result "<summary>" --by <you>

collab.py lock <you> "<file>"                      Acquire exclusive file lock
collab.py unlock <you> "<file>"                    Release file lock

collab.py context set "<key>" "<value>" --by <you> Share persistent info
collab.py context get                              View all shared context

# Lead-only terminal control
collab.py nudge <target> "<msg>"                   Signal + inject poll command
collab.py inject <target> "<prompt>"               Type directly into target terminal
collab.py interrupt <target>                       Send Escape to stop generation
collab.py windows                                  List detected console windows
```

Full protocol documentation: [PROTOCOL.md](PROTOCOL.md)

## License

[Unlicense](LICENSE) — public domain.
