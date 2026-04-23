---
name: ccollab
description: Spawn a multi-instance Claude Code collaboration session (one lead + N dev tabs) using the ccollab launcher. Use when the user wants parallel CC instances coordinating on a task, says "spin up a collab session", "/ccollab", "launch a multi-instance session", or similar. The user controls model selection per role and the lead's initial prompt.
allowed-tools: Bash(ccollab *)
---

# ccollab — multi-instance launcher

Use this skill to launch a CC-Collab collaboration session: one lead instance plus N subordinate (dev) instances, opened together as tabs (Windows Terminal named-window) or as a session (tmux on Linux/macOS). Each instance is its own Claude Code process; they coordinate via shared state files, not via your conversation.

**Prerequisite:** the user must have `pip install -e .` ed this repo so the `ccollab` command is on PATH. If running it errors with "command not found", point them at the README install instructions.

## How to invoke

Run the launcher in **flag mode** (non-interactive). Always pass `--yes` so it doesn't block on a confirmation prompt the Bash tool can't answer.

```bash
ccollab "<absolute-project-dir>" \
    --lead-model <model> \
    --dev-model <model> \
    --devs <N> \
    --lead-role "<one-line role>" \
    --dev-role "<dev1 role>" \
    --dev-role "<dev2 role>" \
    --prompt "<initial prompt for lead>" \
    --yes
```

## Flag reference

| Flag | Required | Default | Notes |
|---|---|---|---|
| `<project-dir>` (positional) | no | cwd | Absolute path. Must already exist. |
| `--lead-model` | no | `opus` | `opus` / `sonnet` / `haiku` aliases stay current. Pinned IDs also accepted. |
| `--dev-model` | no | same as `--lead-model` | One model applies to all devs. |
| `--devs` | no | 2 (or count of `--dev-role`) | # of subordinates. Total instances = `--devs + 1`. |
| `--lead-role` | no | "Coordination, architecture, and task management" | Free text. |
| `--dev-role` | no | sensible defaults | Repeat once per dev, in order. |
| `--prompt` | no | none | Auto-typed into lead's tab once it's ready. |
| `--yes` | **YES** | — | **Always pass this.** Skips the human-confirmation prompt. |

## Decision flow

1. **Gather what the user wants.** Ask only what's missing — don't make them re-specify defaults. Minimum useful info:
   - What should the team work on? (becomes `--prompt`)
   - Anything special about model choice? (else default `opus` for lead, same for devs)
   - How many devs? (else default 2)
2. **Confirm role descriptions only if non-trivial.** For most tasks the defaults are fine. If the project has a clear shape (e.g., a web app), suggest specific roles like "backend", "frontend", "tester".
3. **Run the launcher** with all answers as flags + `--yes`.
4. **Report back to the user**: confirm tabs/sessions opened and note that the lead has been auto-prompted with their task.

## After launch

- **Windows:** one Windows Terminal window named `collab` opens with N tabs (lead is gold, devs are color-coded).
- **Linux with tmux:** one tmux session named `collab` with N windows. Attach with `tmux attach -t collab`.
- **macOS / Linux without tmux:** N separate terminal emulator windows.
- `/effort max` is auto-typed into every instance whose model supports it (Haiku skipped silently).
- The `--prompt` text is auto-typed into the lead's instance after Claude Code reaches its input prompt (~6s after launch).
- The session lives independently of the parent conversation. To stop it later: `ccollab --stop "<project-dir>"`.

## Examples

**Minimal — user says "spin up a collab session to refactor this module":**
```bash
ccollab "/path/to/project" \
    --prompt "Refactor src/parser.py into smaller modules — split lexer from parser, write tests for each" \
    --yes
```

**Mixed-model — Opus lead with two cheap Sonnet devs:**
```bash
ccollab "/path/to/web-app" \
    --lead-model opus --dev-model sonnet --devs 2 \
    --lead-role "architect and reviewer" \
    --dev-role "backend (FastAPI)" --dev-role "frontend (React)" \
    --prompt "Add user auth: design the schema, build the endpoints, wire up the login UI" \
    --yes
```

**Big team — five Sonnet devs led by an Opus, no specific roles:**
```bash
ccollab --lead-model opus --dev-model sonnet --devs 5 \
    --prompt "Triage the open issues in this repo and start fixing the top three by impact" \
    --yes
```

## Pitfalls to avoid

- **Don't omit `--yes`.** The launcher will hang waiting for a confirmation it can't receive through Bash.
- **On Windows in bash, prefer forward slashes for paths** to avoid escaping headaches.
- **Don't run multiple sessions back-to-back without stopping the prior one.** State files get reset on each launch but `--stop` cleans up the project's CLAUDE.md properly.
- **Project dir must already exist.** If the user names a non-existent directory, ask them to create it first — don't silently create it.
