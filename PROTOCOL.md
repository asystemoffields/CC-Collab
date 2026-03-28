# Claude Code Collaboration Protocol

You are participating in a **multi-instance collaboration session**. Multiple Claude Code instances are working on the same project simultaneously, coordinated through a shared state system.

**Harness location:** `collab.py` (in the claude-collab directory)

For brevity in this doc, commands are shown as `collab <cmd>`. In practice, run:
```
python /path/to/claude-collab/collab.py <cmd>
```
(The launcher auto-generates the correct absolute paths in each project's CLAUDE.md.)

---

## Session Startup

When you begin a collaboration session, execute these steps in order:

1. **Join** with a unique, descriptive name:
   ```
   collab join <your-name> --role "<one-line role description>"
   ```
   Good names: `architect`, `backend`, `frontend`, `reviewer`, `researcher`, `tester`

2. **Check the current state:**
   ```
   collab status
   ```

3. **Poll for any pending work:**
   ```
   collab poll <your-name>
   ```

4. **Claim or create tasks** based on what the status shows.

---

## Core Rules

### Always Do
- **Poll regularly.** Run `collab poll <your-name>` every 3-5 interactions. This is how you receive messages and see what others are doing.
- **Lock before editing.** Run `collab lock <your-name> "<filepath>"` before modifying any file. Unlock it with `collab unlock <your-name> "<filepath>"` when done.
- **Share discoveries.** When you learn something other instances need to know, use `collab context set "<key>" "<value>" --by <your-name>`.
- **Communicate actively.** Other instances cannot read your conversation. If you make a decision, change direction, or find something important, tell them.
- **Update your status.** Run `collab heartbeat <your-name> --working-on "<what>"` when you switch tasks.
- **Complete tasks properly.** When finishing a task, include a result: `collab task update <id> done --result "<summary>" --by <your-name>`

### Never Do
- Edit a file another node has locked. Check `collab locks` first. If blocked, send them a message.
- Start work that overlaps with what another node is doing. Check `collab status` and `collab task list` first.
- Go silent for long stretches. If you're blocked or thinking, say so with a broadcast.

---

## Command Reference

### Node Management
```
collab join <name> --role "<role>"           # Register as a node
collab leave <name>                          # Deregister (releases your locks)
collab heartbeat <name> --working-on "<w>"   # Update your current activity
collab heartbeat <name> --status busy        # Set status: active|idle|busy|away
collab status                                # Full overview of everything
```

### Messaging
```
collab send <you> <them> "<message>"         # Direct message
collab broadcast <you> "<message>"           # Message to all nodes
collab inbox <you>                           # New messages since last poll
collab inbox <you> --all                     # All messages ever
collab request <you> <them> "<description>"  # Create task + notify them
```

### Shared Context (persistent key-value store)
```
collab context set "<key>" "<value>" --by <you>     # Store a value
collab context get                                   # List all entries
collab context get "<key>"                           # Get specific value
collab context append "<key>" "<more>" --by <you>    # Append to value
collab context del "<key>"                           # Remove entry
```

Use context for information that persists across the session:
- Architecture decisions (`arch_pattern`, `api_style`)
- Shared configuration (`db_type`, `port`, `base_url`)
- File locations (`schema_path`, `config_path`)
- Conventions (`naming_convention`, `error_handling`)
- Discoveries (`auth_notes`, `perf_findings`)

### Task Board
```
collab task add "<title>" --by <you>                              # Create open task
collab task add "<title>" --assign <node> --priority high --by <you>  # Create + assign
collab task list                                                  # All tasks
collab task list --status open                                    # Filter by status
collab task list --assigned <you>                                 # Your tasks
collab task claim <you> <id>                                      # Claim an open task
collab task update <id> active --by <you>                         # Start working
collab task update <id> done --result "<result>" --by <you>       # Complete
collab task update <id> blocked --result "<reason>" --by <you>    # Mark blocked
collab task show <id>                                             # Full details
```

Task statuses: `open` -> `claimed` -> `active` -> `done` (or `blocked`)

### File Coordination
```
collab lock <you> "<filepath>"     # Acquire exclusive lock
collab unlock <you> "<filepath>"   # Release lock
collab locks                       # List all active locks
```

Lock the specific files you're about to edit, not entire directories.

### Polling & Activity
```
collab pending <you>         # Quick signal check (fast! run after every file write)
collab poll <you>            # Everything new since your last poll (full details)
collab log --limit 30        # Recent activity across all nodes
```

**Signal files:** When someone sends you a message, assigns you a task, or broadcasts,
a signal file is created at `state/_signal_<your-name>`. The `pending` command reads
and clears this file. This means you can detect new work without running the heavier
`poll` command. Run `pending` after every file write; if it shows signals, run `poll`.

---

## Communication Patterns

| Situation | What to do |
|-----------|-----------|
| Need information from a specific node | `collab send <you> <them> "question"` |
| Found something everyone needs to know | `collab context set "key" "value" --by <you>` |
| Made an architectural decision | `collab broadcast <you> "Decision: ..."` |
| Need another node to do something | `collab request <you> <them> "description"` |
| Finished a piece of work | `collab task update <id> done --result "summary" --by <you>` and `collab broadcast <you> "Finished X, ready for Y"` |
| Blocked and need help | `collab broadcast <you> "Blocked on X because Y"` |
| Switching to a different task | `collab heartbeat <you> --working-on "new task"` |

---

## Integration with Project CLAUDE.md

To enable collaboration on a specific project, add this block to that project's `CLAUDE.md`:

```markdown
## Multi-Instance Collaboration

This project uses the Claude Code Collaboration Harness for multi-agent coordination.

Harness: `python /path/to/claude-collab/collab.py`

On session start:
1. Run `python /path/to/claude-collab/collab.py join <your-name> --role "<role>"`
2. Run `python /path/to/claude-collab/collab.py status` to see what's happening
3. Run `python /path/to/claude-collab/collab.py poll <your-name>` for updates

Poll every 3-5 interactions. Lock files before editing. Share context actively.
Full protocol: /path/to/claude-collab/PROTOCOL.md
```

> **Note:** You don't need to write this manually. The `launcher.py` script auto-generates this block with correct absolute paths when you launch a session.

---

## Example: Three-Instance Collaboration

**Instance 1 (architect):**
```bash
collab join architect --role "system design and task coordination"
collab task add "Design database schema" --priority high --by architect
collab task add "Build REST API" --assign backend --priority high --by architect
collab task add "Create React frontend" --assign frontend --priority medium --by architect
collab context set "stack" "FastAPI + React + PostgreSQL" --by architect
collab context set "api_style" "REST, snake_case, JSON responses" --by architect
collab task claim architect 1
collab task update 1 active --by architect
collab lock architect "docs/schema.sql"
# ... design the schema ...
collab unlock architect "docs/schema.sql"
collab task update 1 done --result "Schema: users, posts, comments. See docs/schema.sql" --by architect
collab broadcast architect "Schema is finalized. Backend can start building endpoints."
```

**Instance 2 (backend):**
```bash
collab join backend --role "API implementation"
collab poll backend
# Sees: task #2 assigned, context about stack and api_style
collab task update 2 active --by backend
collab heartbeat backend --working-on "Building REST API endpoints"
collab lock backend "src/api/routes.py"
# ... implement API ...
collab unlock backend "src/api/routes.py"
collab context set "api_endpoints" "GET /posts, POST /posts, GET /users/{id}" --by backend
collab task update 2 done --result "All CRUD endpoints implemented" --by backend
collab broadcast backend "API is up. Frontend can start integrating."
```

**Instance 3 (frontend):**
```bash
collab join frontend --role "React UI development"
collab poll frontend
# Sees: task #3 assigned, waits for API context
collab send frontend backend "What's the base URL for the API?"
# Later, polls and sees the response
collab poll frontend
collab context get "api_endpoints"
collab task update 3 active --by frontend
# ... build the UI ...
```

---

## Lead Instance — Autonomous Management

The `lead` instance is the **autonomous manager** of the collaboration session. It can operate without human intervention, managing dev instances directly.

### Responsibilities

1. **Plan & delegate** — break the user's request into tasks, assign to `dev1`/`dev2`
2. **Build** — do your own implementation (architecture, shared files, complex pieces)
3. **Monitor** — poll regularly, check task progress, unblock stuck devs
4. **Steer** — redirect devs who go off-track using terminal control

### Terminal Control Commands

These commands give lead direct control over other instances' console windows via Win32 `WriteConsoleInput` keystrokes.

```
collab windows                                    # List all detectable consoles
collab inject <target> "<prompt>"                  # Type text + press Enter in target's terminal
collab interrupt <target>                          # Send Escape twice to stop generation
collab nudge <target> "<optional message>"         # Signal + inject a poll command
collab whoami <name>                               # Print color-coded role banner to identify terminal
```

### When to Use What

| Situation | Command | Notes |
|-----------|---------|-------|
| Assigned new tasks, need dev to notice | `nudge <target> "New tasks"` | Safest — signals + injects poll |
| Dev idle for a while, no progress | `nudge <target> "Check for work"` | Try nudge first |
| Dev not responding to nudges | `inject <target> "Poll for new tasks and check your inbox"` | Direct instruction |
| Dev going in wrong direction | `interrupt <target>` then `inject <target> "Stop. Do X instead."` | Interrupt first! |
| Need immediate attention | `interrupt <target>` | Sends Escape twice |
| Routine progress check | `poll lead` + `task list` | No terminal control needed |

### Autonomous Workflow

```
1. Create tasks       →  task add "..." --assign dev1 --by lead
2. Nudge devs         →  nudge dev1 "New tasks assigned"
3. Do your own work   →  (build, write code, etc.)
4. Check progress     →  poll lead / task list
5. Dev finishes       →  assign follow-up, nudge again
6. Dev stuck          →  send message, then nudge
7. Dev off-track      →  interrupt → wait 2s → inject correction
```

### Safety Rules

- **Always `interrupt` before `inject`** if the target is mid-generation — otherwise your injected text interleaves with their output
- **Wait ~2 seconds** after interrupting before injecting, so the instance settles
- **Prefer `nudge` over `inject`** for routine coordination — it's lighter and less disruptive
- **Never inject while a dev is actively writing a file** — check `locks` first
- `inject` types character by character and presses Enter — the target processes it exactly as if a human typed it

---

## Tips for Effective Collaboration

1. **Be specific in context keys.** Use `db_connection_string` not just `db`.
2. **Keep messages actionable.** "Schema is at docs/schema.sql, uses UUID primary keys" beats "I updated the schema."
3. **Use task results.** When marking done, summarize what was accomplished so others can build on it.
4. **Lock granularly.** Lock individual files, not entire directories.
5. **Broadcast breaking changes.** If you change an interface others depend on, tell everyone immediately.
6. **Poll after sending requests.** If you request work from another node, poll again after a while to see their response.
