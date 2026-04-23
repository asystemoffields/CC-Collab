# CC-Collab

Multi-instance Claude Code collaboration. Launches N Claude Code instances that coordinate in real-time through a shared file-based state system — one leads, the rest develop, all communicate autonomously.

## How It Works

The launcher opens terminal windows, each running Claude Code with a designated role (**lead**, **dev1**, **dev2**, ...). A shared `state/` directory holds JSON files for messages, tasks, file locks, and context. Instances coordinate by polling this state — no server, no network, just files.

The **lead** instance acts as an autonomous manager: it breaks down the user's request into tasks, assigns them to dev instances, and can directly control their terminals (inject prompts, interrupt generation, nudge for attention) via platform-native APIs.

The launcher auto-injects collaboration instructions into the target project's `CLAUDE.md`, so each instance knows how to participate without manual setup.

## Requirements

- **Python 3.12+** (stdlib only, zero dependencies)
- **Claude Code CLI** installed and authenticated
- **Terminal injection** (one of):
  - **Windows**: Win32 API via ctypes (automatic)
  - **Linux/macOS**: `tmux` (preferred) or GNU `screen`

## Quick Start

```bash
git clone https://github.com/asystemoffields/CC-Collab.git
cd CC-Collab
pip install -e .         # exposes the `ccollab` command on PATH
```

Three ways to launch:

**Interactive wizard** — best for normal use. From any project directory:
```bash
cd /path/to/your/project
ccollab
```
The wizard walks you through model selection (lead + subordinates separately), number of subordinates, role descriptions, and the lead's initial prompt. Tabs auto-open in one Windows Terminal window (or one tmux session on Linux), `/effort max` is auto-injected where supported, and your prompt is auto-typed into the lead.

**Flag mode** — non-interactive, scriptable, also how the `/ccollab` skill calls it:
```bash
ccollab --lead-model opus --dev-model sonnet --devs 2 \
        --lead-role "architect" \
        --dev-role "backend" --dev-role "frontend" \
        --prompt "build the auth flow" \
        --yes
```
See `ccollab --help` for all flags.

**Slash command from inside any Claude Code session** — install the skill and type `/ccollab` (or just describe the task you want a team to work on). See [Slash Command (skill)](#slash-command-skill) below.

**Legacy single-flag invocation** still works for back-compat:
```bash
launch.bat                                    # double-click
python launcher.py "/path/to/project" -n 5    # 5 nodes, no wizard
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `COLLAB_MODEL` | `opus` | Default model when wizard/flag mode don't override (legacy single-model fallback) |
| `COLLAB_SKIP_PERMISSIONS` | `1` | Set to `0` to require permission prompts |
| `COLLAB_STATE_DIR` | `./state` | Directory for collaboration state files |

## Slash Command (skill)

A Claude Code skill ships with this repo at [`skills/ccollab/SKILL.md`](skills/ccollab/SKILL.md). Install it once and you can launch a multi-instance session from inside any other Claude Code session by typing `/ccollab` (or just describing what you want a team to work on).

Install (user-level, available in all your projects):
```bash
# Linux/macOS
mkdir -p ~/.claude/skills/ccollab
cp skills/ccollab/SKILL.md ~/.claude/skills/ccollab/

# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\ccollab" | Out-Null
Copy-Item skills/ccollab/SKILL.md "$env:USERPROFILE\.claude\skills\ccollab\"
```

The skill teaches Claude to gather any missing inputs from the conversation, then call `ccollab` in flag mode with `--yes`. Requires `pip install -e .` of this repo so `ccollab` is on PATH.

## Architecture

```
launcher.py          Wizard, flag mode, state reset, CLAUDE.md write, N tab spawn
collab.py            CLI tool used by each instance for all coordination
inject.py            Cross-platform terminal injection (Win32, tmux, screen)
test_collab.py       Comprehensive test suite
pyproject.toml       pip install support (ccollab + collab CLI entry points)
skills/ccollab/      User-installable Claude Code skill for /ccollab slash command
state/
  nodes.json         Active instances and their roles
  messages.json      Direct and broadcast messages
  tasks.json         Task board (open/claimed/active/done/blocked)
  locks.json         File-level exclusive locks (auto-expire after 30m)
  context.json       Shared key-value store for decisions/config
  log.json           Activity feed
  meta.json          Internal metadata (task ID counter)
  _signal_<name>     Per-node signal files for fast change detection
```

All state access uses OS-level file locking for safe concurrent writes.

## Command Reference

### Command Aliases

Frequent commands have short aliases to save tokens:

| Alias | Command |
|-------|---------|
| `s` | `status` |
| `p` | `poll` |
| `pd` | `pending` |
| `b` | `broadcast` |
| `t` | `task` |
| `c` | `context` |
| `h` | `health` |
| `w` | `windows` |
| `n` | `nudge` |

### Nodes

```
collab.py join <name> --role "<role>"              Register as a node
collab.py leave <name>                             Leave (releases your locks)
collab.py status                                   Full session overview
collab.py health                                   Node health check (heartbeat, load)
collab.py summary                                  Session report (completed work, stats)
collab.py whoami <name>                            Print role banner
```

### Communication

```
collab.py send <you> <them> "<msg>"                Direct message
collab.py broadcast <you> "<msg>"                  Message all nodes
collab.py inbox <you> [--all]                      Check messages
collab.py poll <name>                              Get all updates since last poll
collab.py pending <name>                           Quick signal check (fast)
```

### Tasks

```
collab.py task add "<title>" --assign <node> --priority high --by <you>
collab.py task add "<title>" --depends-on 3,5      Create with dependencies
collab.py task list [--status done] [--assigned bob]
collab.py task show <id>                           Full task details
collab.py task claim <you> <id>                    Claim an open task
collab.py task update <id> done --result "<summary>" --by <you>
collab.py task comment <id> "message" --by <you>   Add a comment
collab.py task reassign <id> <new_node> --by <you> Reassign to another node
```

Tasks are sorted by status (active > blocked > claimed > open > done), then by priority (critical > high > medium > low).

### File Locks

```
collab.py lock <you> "<file>"                      Acquire exclusive file lock
collab.py unlock <you> "<file>"                    Release file lock
collab.py locks                                    List all active locks
```

Locks auto-expire after 30 minutes. Leaving the session releases all your locks.

### Context

```
collab.py context set "<key>" "<value>" --by <you> Share persistent info
collab.py context get [<key>]                      View shared context
collab.py context del <key>                        Delete a key
collab.py context append <key> "<value>" --by <you> Append to existing value
```

### Lead-Only Terminal Control

```
collab.py nudge <target> "<msg>"                   Signal + inject poll command
collab.py inject <target> "<prompt>"               Type directly into target terminal
collab.py interrupt <target>                       Send Escape to stop generation
collab.py windows                                  List detected console windows
```

### Other

```
collab.py log [--limit 50]                         View activity log
collab.py request <you> <them> "<desc>"            Request work (creates task + message)
collab.py heartbeat <name> --working-on "..." --status busy
collab.py reset --confirm                          Clear all state (destructive)
```

## v2.0.0 Features

- **Cross-platform**: Windows (Win32 API), Linux/macOS (tmux, GNU screen)
- **N-node scaling**: Launch up to 8 dev nodes with `--nodes N` flag
- **Task dependencies**: `--depends-on 3,5` blocks a task until its dependencies are done
- **Task comments**: `task comment <id> "text"` for inline discussion
- **Task reassignment**: `task reassign <id> <node>` to hand off work
- **Command aliases**: Single-letter shortcuts (`s`, `p`, `t`, `c`, `h`, etc.)
- **Stale node detection**: Poll output warns when nodes miss heartbeats (>5 min)
- **Lock expiry**: File locks auto-release after 30 minutes
- **Improved poll output**: Shows your assigned tasks with dependency/blocking status
- **Health command**: Quick view of all nodes' heartbeat age, task load, lock count
- **Summary command**: Session report with per-node breakdown and completion stats
- **Smart task sorting**: Tasks sorted by status priority, then by urgency
- **State validation**: Verify and repair state file integrity
- **pip installable**: `pip install .` for `collab` and `claude-collab` CLI commands
- **Comprehensive test suite**: pytest coverage of all commands and edge cases

Full protocol documentation: [PROTOCOL.md](PROTOCOL.md)

## Testing

```bash
pip install pytest
python -m pytest test_collab.py -v
```

## License

[Unlicense](LICENSE) — public domain.
