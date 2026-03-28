#!/usr/bin/env python3
"""
Comprehensive test suite for the Claude Code Collaboration Harness (collab.py).

Covers: utilities, signal files, file locking, state management,
all commands (nodes, messages, context, tasks, locks, poll, pending,
log, request, reset, whoami), and the CLI parser.

Run:  python -m pytest test_collab.py -v
"""

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import collab


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def state_dir(tmp_path):
    """Create a fresh temporary state directory for each test."""
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def state(state_dir):
    """Return a State instance backed by a temp directory."""
    return collab.State(state_dir)


@pytest.fixture
def populated_state(state):
    """State with two registered nodes, a task, and a message."""
    collab.cmd_join(state, "alice", "architect")
    collab.cmd_join(state, "bob", "developer")
    return state


@pytest.fixture
def capture_stdout():
    """Context-manager helper to capture stdout."""
    class Capture:
        def __enter__(self):
            self.buf = StringIO()
            self._patch = mock.patch("sys.stdout", self.buf)
            self._patch.start()
            return self

        def __exit__(self, *exc):
            self._patch.stop()

        @property
        def text(self):
            return self.buf.getvalue()

    return Capture


# ══════════════════════════════════════════════════════════════════
#  UTILITY TESTS
# ══════════════════════════════════════════════════════════════════

class TestUtilities:

    def test_utcnow_is_isoformat(self):
        ts = collab.utcnow()
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None  # timezone-aware

    def test_parse_ts_roundtrip(self):
        ts = collab.utcnow()
        dt = collab.parse_ts(ts)
        assert isinstance(dt, datetime)
        assert dt.tzinfo is not None

    def test_ago_just_now(self):
        ts = collab.utcnow()
        result = collab.ago(ts)
        assert result in ("just now", "0s ago", "1s ago", "2s ago")

    def test_ago_minutes(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = collab.ago(past)
        assert "m ago" in result

    def test_ago_hours(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        result = collab.ago(past)
        assert "h ago" in result

    def test_ago_days(self):
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        result = collab.ago(past)
        assert "d ago" in result

    def test_ago_bad_input(self):
        result = collab.ago("not-a-timestamp")
        assert result == "?"

    def test_short_time_format(self):
        ts = "2025-01-15T14:30:45+00:00"
        result = collab.short_time(ts)
        assert result == "14:30:45"

    def test_short_time_bad_input(self):
        result = collab.short_time("bad")
        assert result == "??:??:??"

    def test_trunc_short_string(self):
        assert collab.trunc("hello", 10) == "hello"

    def test_trunc_exact_length(self):
        assert collab.trunc("12345", 5) == "12345"

    def test_trunc_long_string(self):
        result = collab.trunc("hello world, this is a long string", 15)
        assert len(result) == 15
        assert result.endswith("...")

    def test_trunc_default_limit(self):
        short = "x" * 60
        assert collab.trunc(short) == short
        long = "x" * 100
        assert len(collab.trunc(long)) == 60


# ══════════════════════════════════════════════════════════════════
#  SIGNAL FILE TESTS
# ══════════════════════════════════════════════════════════════════

class TestSignalFiles:

    def test_signal_creates_file(self, state_dir):
        collab.signal_node(state_dir, "alice", "test reason")
        path = state_dir / "_signal_alice"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "test reason" in content

    def test_signal_appends(self, state_dir):
        collab.signal_node(state_dir, "alice", "reason1")
        collab.signal_node(state_dir, "alice", "reason2")
        content = (state_dir / "_signal_alice").read_text(encoding="utf-8")
        assert "reason1" in content
        assert "reason2" in content

    def test_read_and_clear_signal(self, state_dir):
        collab.signal_node(state_dir, "bob", "wake up")
        lines = collab.read_and_clear_signal(state_dir, "bob")
        assert len(lines) == 1
        assert "wake up" in lines[0]
        # File should be deleted
        assert not (state_dir / "_signal_bob").exists()

    def test_read_and_clear_no_signal(self, state_dir):
        lines = collab.read_and_clear_signal(state_dir, "nobody")
        assert lines == []

    def test_signal_multiple_then_clear(self, state_dir):
        for i in range(5):
            collab.signal_node(state_dir, "alice", f"reason {i}")
        lines = collab.read_and_clear_signal(state_dir, "alice")
        assert len(lines) == 5


# ══════════════════════════════════════════════════════════════════
#  FILE LOCK TESTS
# ══════════════════════════════════════════════════════════════════

class TestFileLock:

    def test_lock_acquire_release(self, tmp_path):
        target = tmp_path / "data.json"
        target.write_text("{}")
        lock = collab.FileLock(target)
        with lock:
            assert lock.lockpath.exists()
        assert not lock.lockpath.exists()

    def test_lock_stale_cleanup(self, tmp_path):
        target = tmp_path / "data.json"
        target.write_text("{}")
        lock = collab.FileLock(target)
        # Create a stale lock file manually
        lock.lockpath.write_text("12345")
        # Set mtime far in the past
        old_time = time.time() - collab.STALE_LOCK_SEC - 5
        os.utime(str(lock.lockpath), (old_time, old_time))
        # Should succeed because the stale lock gets cleaned up
        with lock:
            pass

    def test_lock_timeout(self, tmp_path):
        target = tmp_path / "data.json"
        target.write_text("{}")
        lock = collab.FileLock(target)
        # Create a fresh lock file (not stale)
        lock.lockpath.write_text("99999")
        # Temporarily reduce timeout
        orig = collab.LOCK_TIMEOUT
        collab.LOCK_TIMEOUT = 0.1
        try:
            with pytest.raises(TimeoutError):
                with lock:
                    pass
        finally:
            collab.LOCK_TIMEOUT = orig
            lock.lockpath.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════
#  STATE MANAGER TESTS
# ══════════════════════════════════════════════════════════════════

class TestState:

    def test_init_creates_default_files(self, state_dir):
        s = collab.State(state_dir)
        for name in collab._DEFAULTS:
            assert (state_dir / f"{name}.json").exists()

    def test_read_write_roundtrip(self, state):
        state.write("context", {"key": "value"})
        data = state.read("context")
        assert data == {"key": "value"}

    def test_update_atomicity(self, state):
        state.write("nodes", {"a": 1})
        def add_b(nodes):
            nodes["b"] = 2
            return "done"
        result = state.update("nodes", add_b)
        assert result == "done"
        nodes = state.read("nodes")
        assert nodes == {"a": 1, "b": 2}

    def test_append_log(self, state):
        state.append_log("alice", "test", "Alice did something")
        log = state.read("log")
        assert len(log) == 1
        assert log[0]["actor"] == "alice"
        assert log[0]["summary"] == "Alice did something"

    def test_append_log_truncation(self, state):
        orig = collab.LOG_MAX
        collab.LOG_MAX = 5
        try:
            for i in range(10):
                state.append_log("bot", "action", f"entry {i}")
            log = state.read("log")
            assert len(log) == 5
            assert log[0]["summary"] == "entry 5"  # oldest kept
        finally:
            collab.LOG_MAX = orig

    def test_next_task_id_increments(self, state):
        id1 = state.next_task_id()
        id2 = state.next_task_id()
        id3 = state.next_task_id()
        assert id1 == 1
        assert id2 == 2
        assert id3 == 3

    def test_read_corrupt_file_returns_default(self, state_dir):
        s = collab.State(state_dir)
        # Corrupt the nodes file
        (state_dir / "nodes.json").write_text("NOT JSON!", encoding="utf-8")
        data = s.read("nodes")
        assert data == {}  # default for nodes is {}

    def test_state_creates_parent_dirs(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "state"
        s = collab.State(deep)
        assert deep.exists()

    def test_write_raw_atomic(self, state):
        """Ensure write uses tmp + rename pattern."""
        path = state._path("nodes")
        state.write("nodes", {"test": True})
        # tmp file should not remain
        assert not path.with_suffix(".tmp").exists()
        assert state.read("nodes") == {"test": True}


# ══════════════════════════════════════════════════════════════════
#  NODE COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestNodeCommands:

    def test_join(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_join(state, "alice", "architect")
        assert "[OK]" in cap.text
        assert "alice" in cap.text
        nodes = state.read("nodes")
        assert "alice" in nodes
        assert nodes["alice"]["role"] == "architect"
        assert nodes["alice"]["status"] == "active"

    def test_join_rejoin(self, state, capture_stdout):
        collab.cmd_join(state, "alice", "architect")
        with capture_stdout() as cap:
            collab.cmd_join(state, "alice", "lead architect")
        assert "Rejoined" in cap.text
        nodes = state.read("nodes")
        assert nodes["alice"]["role"] == "lead architect"

    def test_leave(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_leave(populated_state, "alice")
        assert "[OK]" in cap.text
        nodes = populated_state.read("nodes")
        assert "alice" not in nodes

    def test_leave_releases_locks(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "main.py")
        with capture_stdout() as cap:
            collab.cmd_leave(populated_state, "alice")
        assert "Released 1 file lock" in cap.text
        locks = populated_state.read("locks")
        assert "main.py" not in locks

    def test_leave_unknown_node(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_leave(state, "nobody")
        assert "WARN" in cap.text

    def test_heartbeat(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_heartbeat(populated_state, "alice",
                                 working_on="refactoring", node_status="busy")
        assert "[OK]" in cap.text
        nodes = populated_state.read("nodes")
        assert nodes["alice"]["working_on"] == "refactoring"
        assert nodes["alice"]["status"] == "busy"

    def test_heartbeat_unknown_node(self, state, capture_stdout):
        with pytest.raises(SystemExit):
            collab.cmd_heartbeat(state, "ghost")


# ══════════════════════════════════════════════════════════════════
#  MESSAGE COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestMessageCommands:

    def test_send(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_send(populated_state, "alice", "bob", "Hello Bob!")
        assert "[OK]" in cap.text
        messages = populated_state.read("messages")
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello Bob!"
        assert messages[0]["from"] == "alice"
        assert messages[0]["to"] == "bob"

    def test_send_signals_recipient(self, populated_state):
        collab.cmd_send(populated_state, "alice", "bob", "Heads up!")
        # Signal file should exist for bob
        signals = collab.read_and_clear_signal(populated_state.dir, "bob")
        assert any("alice" in s for s in signals)

    def test_send_to_unknown_node(self, populated_state):
        with pytest.raises(SystemExit):
            collab.cmd_send(populated_state, "alice", "ghost", "Hi")

    def test_broadcast(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_broadcast(populated_state, "alice", "Everyone listen!")
        assert "[OK]" in cap.text
        assert "1 other node" in cap.text
        messages = populated_state.read("messages")
        assert messages[-1]["to"] == "all"
        assert messages[-1]["type"] == "broadcast"

    def test_inbox_new_messages(self, populated_state, capture_stdout):
        collab.cmd_send(populated_state, "alice", "bob", "Message 1")
        collab.cmd_send(populated_state, "alice", "bob", "Message 2")
        with capture_stdout() as cap:
            collab.cmd_inbox(populated_state, "bob")
        assert "Message 1" in cap.text
        assert "Message 2" in cap.text

    def test_inbox_no_messages(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_inbox(populated_state, "alice")
        assert "No new messages" in cap.text

    def test_inbox_all_flag(self, populated_state, capture_stdout):
        collab.cmd_send(populated_state, "alice", "bob", "Old message")
        # Advance bob's last_poll so the message is "old"
        collab.cmd_poll(populated_state, "bob")
        with capture_stdout() as cap:
            collab.cmd_inbox(populated_state, "bob", show_all=True)
        assert "Old message" in cap.text

    def test_message_truncation(self, populated_state):
        """Messages list shouldn't exceed MSG_MAX."""
        orig = collab.MSG_MAX
        collab.MSG_MAX = 3
        try:
            for i in range(5):
                collab.cmd_send(populated_state, "alice", "bob", f"msg {i}")
            messages = populated_state.read("messages")
            assert len(messages) == 3
        finally:
            collab.MSG_MAX = orig


# ══════════════════════════════════════════════════════════════════
#  CONTEXT COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestContextCommands:

    def test_context_set_and_get(self, state, capture_stdout):
        collab.cmd_context_set(state, "db_type", "postgres", by="alice")
        with capture_stdout() as cap:
            collab.cmd_context_get(state, "db_type")
        assert "postgres" in cap.text
        assert "alice" in cap.text

    def test_context_get_all(self, state, capture_stdout):
        collab.cmd_context_set(state, "key1", "val1", by="a")
        collab.cmd_context_set(state, "key2", "val2", by="b")
        with capture_stdout() as cap:
            collab.cmd_context_get(state)
        assert "key1" in cap.text
        assert "key2" in cap.text

    def test_context_get_missing_key(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_context_get(state, "nonexistent")

    def test_context_get_empty(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_context_get(state)
        assert "No shared context" in cap.text

    def test_context_del(self, state, capture_stdout):
        collab.cmd_context_set(state, "temp", "value", by="alice")
        with capture_stdout() as cap:
            collab.cmd_context_del(state, "temp")
        assert "[OK]" in cap.text
        ctx = state.read("context")
        assert "temp" not in ctx

    def test_context_del_missing(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_context_del(state, "ghost")

    def test_context_append(self, state, capture_stdout):
        collab.cmd_context_set(state, "notes", "line1", by="alice")
        with capture_stdout() as cap:
            collab.cmd_context_append(state, "notes", "line2", by="bob")
        assert "[OK]" in cap.text
        ctx = state.read("context")
        assert "line1\nline2" == ctx["notes"]["value"]
        assert ctx["notes"]["set_by"] == "bob"

    def test_context_append_new_key(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_context_append(state, "new_key", "first", by="alice")
        assert "[OK]" in cap.text
        ctx = state.read("context")
        assert ctx["new_key"]["value"] == "first"


# ══════════════════════════════════════════════════════════════════
#  TASK COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestTaskCommands:

    def test_task_add_unassigned(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_task_add(state, "Build API", by="alice")
        assert "Task #1" in cap.text
        tasks = state.read("tasks")
        assert "1" in tasks
        assert tasks["1"]["status"] == "open"
        assert tasks["1"]["assigned_to"] is None

    def test_task_add_assigned(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_task_add(populated_state, "Write tests",
                                assign="bob", priority="high", by="alice")
        assert "assigned to bob" in cap.text
        tasks = populated_state.read("tasks")
        t = tasks["1"]
        assert t["status"] == "claimed"
        assert t["assigned_to"] == "bob"
        assert t["priority"] == "high"

    def test_task_add_signals_assignee(self, populated_state):
        collab.cmd_task_add(populated_state, "Do something",
                            assign="bob", by="alice")
        signals = collab.read_and_clear_signal(populated_state.dir, "bob")
        assert any("Task #" in s for s in signals)

    def test_task_list(self, state, capture_stdout):
        collab.cmd_task_add(state, "Task A", by="alice")
        collab.cmd_task_add(state, "Task B", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_list(state)
        assert "Task A" in cap.text
        assert "Task B" in cap.text

    def test_task_list_empty(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_task_list(state)
        assert "No tasks found" in cap.text

    def test_task_list_filter_status(self, state, capture_stdout):
        collab.cmd_task_add(state, "Open task", by="a")
        collab.cmd_task_add(state, "Done task", by="a")
        collab.cmd_task_update(state, 2, "done", by="a")
        with capture_stdout() as cap:
            collab.cmd_task_list(state, status_filter="done")
        assert "Done task" in cap.text
        assert "Open task" not in cap.text

    def test_task_list_filter_assigned(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "For bob", assign="bob", by="alice")
        collab.cmd_task_add(populated_state, "For alice", assign="alice", by="bob")
        with capture_stdout() as cap:
            collab.cmd_task_list(populated_state, assigned_filter="bob")
        assert "For bob" in cap.text
        assert "For alice" not in cap.text

    def test_task_claim(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Open task", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_claim(populated_state, "bob", 1)
        assert "[OK]" in cap.text
        tasks = populated_state.read("tasks")
        assert tasks["1"]["assigned_to"] == "bob"
        assert tasks["1"]["status"] == "claimed"

    def test_task_claim_not_found(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_task_claim(state, "alice", 999)

    def test_task_claim_already_done(self, state, capture_stdout):
        collab.cmd_task_add(state, "Done task", by="a")
        collab.cmd_task_update(state, 1, "done", by="a")
        with pytest.raises(SystemExit):
            collab.cmd_task_claim(state, "bob", 1)

    def test_task_update(self, state, capture_stdout):
        collab.cmd_task_add(state, "A task", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_update(state, 1, "active", by="alice")
        assert "active" in cap.text
        tasks = state.read("tasks")
        assert tasks["1"]["status"] == "active"

    def test_task_update_with_result(self, state, capture_stdout):
        collab.cmd_task_add(state, "A task", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_update(state, 1, "done",
                                   result_text="Completed successfully", by="alice")
        tasks = state.read("tasks")
        assert tasks["1"]["result"] == "Completed successfully"

    def test_task_update_not_found(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_task_update(state, 999, "done", by="alice")

    def test_task_show(self, state, capture_stdout):
        collab.cmd_task_add(state, "Show me", desc="A detailed description",
                            assign="bob", priority="high", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_show(state, 1)
        assert "Show me" in cap.text
        assert "high" in cap.text
        assert "bob" in cap.text
        assert "A detailed description" in cap.text
        assert "History:" in cap.text

    def test_task_show_not_found(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_task_show(state, 999)

    def test_task_history_tracking(self, state):
        collab.cmd_task_add(state, "Track me", by="alice")
        collab.cmd_task_update(state, 1, "active", by="alice")
        collab.cmd_task_update(state, 1, "done", result_text="Done!", by="alice")
        tasks = state.read("tasks")
        history = tasks["1"]["history"]
        assert len(history) == 3  # created, open->active, active->done
        assert history[0]["action"] == "created"
        assert "active" in history[1]["action"]
        assert "done" in history[2]["action"]


# ══════════════════════════════════════════════════════════════════
#  FILE LOCK COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestLockCommands:

    def test_lock(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_lock(populated_state, "alice", "main.py")
        assert "[OK]" in cap.text
        locks = populated_state.read("locks")
        assert "main.py" in locks
        assert locks["main.py"]["held_by"] == "alice"

    def test_lock_already_held_by_self(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "main.py")
        with capture_stdout() as cap:
            collab.cmd_lock(populated_state, "alice", "main.py")
        assert "Already locked by you" in cap.text

    def test_lock_held_by_other(self, populated_state):
        collab.cmd_lock(populated_state, "alice", "main.py")
        with pytest.raises(SystemExit):
            collab.cmd_lock(populated_state, "bob", "main.py")

    def test_unlock(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "main.py")
        with capture_stdout() as cap:
            collab.cmd_unlock(populated_state, "alice", "main.py")
        assert "[OK]" in cap.text
        assert populated_state.read("locks") == {}

    def test_unlock_not_locked(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_unlock(populated_state, "alice", "main.py")
        assert "was not locked" in cap.text

    def test_unlock_held_by_other(self, populated_state):
        collab.cmd_lock(populated_state, "alice", "main.py")
        with pytest.raises(SystemExit):
            collab.cmd_unlock(populated_state, "bob", "main.py")

    def test_locks_list(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "file1.py")
        collab.cmd_lock(populated_state, "bob", "file2.py")
        with capture_stdout() as cap:
            collab.cmd_locks(populated_state)
        assert "file1.py" in cap.text
        assert "file2.py" in cap.text
        assert "alice" in cap.text
        assert "bob" in cap.text

    def test_locks_empty(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_locks(state)
        assert "No active file locks" in cap.text


# ══════════════════════════════════════════════════════════════════
#  POLL & PENDING COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestPollAndPending:

    def test_poll_no_updates(self, populated_state, capture_stdout):
        # Poll immediately after join — should see no updates from "others"
        with capture_stdout() as cap:
            collab.cmd_poll(populated_state, "alice")
        # alice's join created activity, but poll filters out own activity
        # bob's join activity might be visible though
        # Either way, it should not error
        assert "alice" not in cap.text or "Updates" in cap.text or "No updates" in cap.text

    def test_poll_sees_new_messages(self, populated_state, capture_stdout):
        # Reset alice's poll timestamp
        collab.cmd_poll(populated_state, "alice")
        collab.cmd_send(populated_state, "bob", "alice", "Check this out!")
        with capture_stdout() as cap:
            collab.cmd_poll(populated_state, "alice")
        assert "Check this out!" in cap.text

    def test_poll_advances_last_poll(self, populated_state):
        nodes_before = populated_state.read("nodes")
        t1 = nodes_before["alice"]["last_poll"]
        time.sleep(0.05)
        collab.cmd_poll(populated_state, "alice")
        nodes_after = populated_state.read("nodes")
        t2 = nodes_after["alice"]["last_poll"]
        assert t2 > t1

    def test_poll_clears_signals(self, populated_state):
        collab.signal_node(populated_state.dir, "alice", "wake up")
        assert (populated_state.dir / "_signal_alice").exists()
        collab.cmd_poll(populated_state, "alice")
        assert not (populated_state.dir / "_signal_alice").exists()

    def test_poll_unknown_node(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_poll(state, "ghost")

    def test_pending_nothing(self, populated_state, capture_stdout):
        # First clear any existing signals from join
        collab.read_and_clear_signal(populated_state.dir, "alice")
        collab.cmd_poll(populated_state, "alice")  # reset last_poll
        with capture_stdout() as cap:
            collab.cmd_pending(populated_state, "alice")
        assert "Nothing pending" in cap.text

    def test_pending_with_signals(self, populated_state, capture_stdout):
        collab.cmd_poll(populated_state, "alice")  # reset
        collab.signal_node(populated_state.dir, "alice", "new task")
        with capture_stdout() as cap:
            collab.cmd_pending(populated_state, "alice")
        assert "signal" in cap.text.lower()

    def test_pending_with_messages(self, populated_state, capture_stdout):
        collab.cmd_poll(populated_state, "alice")  # reset
        collab.cmd_send(populated_state, "bob", "alice", "Hey!")
        # Clear the signal so we only test message detection
        collab.read_and_clear_signal(populated_state.dir, "alice")
        with capture_stdout() as cap:
            collab.cmd_pending(populated_state, "alice")
        assert "message" in cap.text.lower()

    def test_pending_unknown_node(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_pending(state, "ghost")


# ══════════════════════════════════════════════════════════════════
#  LOG COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestLogCommand:

    def test_log_empty(self, state, capture_stdout):
        # Clear the default log
        state.write("log", [])
        with capture_stdout() as cap:
            collab.cmd_log(state)
        assert "No activity" in cap.text

    def test_log_shows_entries(self, state, capture_stdout):
        state.append_log("alice", "test", "Alice did something")
        state.append_log("bob", "test", "Bob did something")
        with capture_stdout() as cap:
            collab.cmd_log(state)
        assert "Alice did something" in cap.text
        assert "Bob did something" in cap.text

    def test_log_limit(self, state, capture_stdout):
        for i in range(10):
            state.append_log("bot", "test", f"Entry {i}")
        with capture_stdout() as cap:
            collab.cmd_log(state, limit=3)
        assert "Entry 9" in cap.text
        assert "Entry 7" in cap.text
        assert "Entry 0" not in cap.text


# ══════════════════════════════════════════════════════════════════
#  REQUEST COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestRequestCommand:

    def test_request_creates_task_and_message(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_request(populated_state, "alice", "bob", "Review PR #42")
        assert "[OK]" in cap.text
        # Should have created a task
        tasks = populated_state.read("tasks")
        assert len(tasks) == 1
        t = list(tasks.values())[0]
        assert t["title"] == "Review PR #42"
        assert t["assigned_to"] == "bob"
        assert t["priority"] == "high"
        assert t["status"] == "claimed"
        # Should have sent a message
        messages = populated_state.read("messages")
        assert any("Review PR #42" in m["content"] for m in messages)

    def test_request_signals_target(self, populated_state):
        collab.cmd_request(populated_state, "alice", "bob", "Help me")
        signals = collab.read_and_clear_signal(populated_state.dir, "bob")
        assert len(signals) > 0

    def test_request_to_unknown_node(self, populated_state):
        with pytest.raises(SystemExit):
            collab.cmd_request(populated_state, "alice", "ghost", "Help")


# ══════════════════════════════════════════════════════════════════
#  STATUS COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestStatusCommand:

    def test_status_empty(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_status(state)
        assert "Collaboration Status" in cap.text
        assert "0 node(s)" in cap.text

    def test_status_with_data(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "A task", by="alice")
        collab.cmd_context_set(populated_state, "key", "val", by="alice")
        collab.cmd_lock(populated_state, "alice", "test.py")
        with capture_stdout() as cap:
            collab.cmd_status(populated_state)
        assert "2 node(s)" in cap.text
        assert "alice" in cap.text
        assert "bob" in cap.text
        assert "A task" in cap.text
        assert "key" in cap.text
        assert "test.py" in cap.text


# ══════════════════════════════════════════════════════════════════
#  RESET COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestResetCommand:

    def test_reset_without_confirm(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_reset(state, confirm=False)

    def test_reset_with_confirm(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "A task", by="alice")
        with capture_stdout() as cap:
            collab.cmd_reset(populated_state, confirm=True)
        assert "[OK]" in cap.text
        # Everything should be cleared
        assert populated_state.read("nodes") == {}
        assert populated_state.read("tasks") == {}
        assert populated_state.read("messages") == []


# ══════════════════════════════════════════════════════════════════
#  WHOAMI COMMAND TESTS
# ══════════════════════════════════════════════════════════════════

class TestWhoamiCommand:

    def test_whoami_registered(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_whoami(populated_state, "alice")
        assert "architect" in cap.text
        assert "alice" in cap.text

    def test_whoami_unregistered(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_whoami(state, "nobody")
        assert "not registered" in cap.text


# ══════════════════════════════════════════════════════════════════
#  CLI PARSER TESTS
# ══════════════════════════════════════════════════════════════════

class TestCLIParser:

    @pytest.fixture
    def parser(self):
        return collab.build_parser()

    def test_join_command(self, parser):
        args = parser.parse_args(["join", "alice", "--role", "architect"])
        assert args.command == "join"
        assert args.name == "alice"
        assert args.role == "architect"

    def test_leave_command(self, parser):
        args = parser.parse_args(["leave", "alice"])
        assert args.command == "leave"
        assert args.name == "alice"

    def test_status_command(self, parser):
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_heartbeat_command(self, parser):
        args = parser.parse_args(["heartbeat", "alice", "--working-on", "tests",
                                  "--status", "busy"])
        assert args.command == "heartbeat"
        assert args.working_on == "tests"
        assert args.node_status == "busy"

    def test_send_command(self, parser):
        args = parser.parse_args(["send", "alice", "bob", "hello"])
        assert args.command == "send"
        assert args.from_node == "alice"
        assert args.to == "bob"
        assert args.message == "hello"

    def test_broadcast_command(self, parser):
        args = parser.parse_args(["broadcast", "alice", "Attention!"])
        assert args.command == "broadcast"
        assert args.from_node == "alice"
        assert args.message == "Attention!"

    def test_inbox_command(self, parser):
        args = parser.parse_args(["inbox", "alice", "--all", "--limit", "50"])
        assert args.command == "inbox"
        assert args.name == "alice"
        assert args.show_all is True
        assert args.limit == 50

    def test_context_set_command(self, parser):
        args = parser.parse_args(["context", "set", "db", "pg", "--by", "alice"])
        assert args.command == "context"
        assert args.context_cmd == "set"
        assert args.key == "db"
        assert args.value == "pg"
        assert args.by == "alice"

    def test_context_get_command(self, parser):
        args = parser.parse_args(["context", "get", "db"])
        assert args.context_cmd == "get"
        assert args.key == "db"

    def test_context_get_all_command(self, parser):
        args = parser.parse_args(["context", "get"])
        assert args.context_cmd == "get"
        assert args.key is None

    def test_context_del_command(self, parser):
        args = parser.parse_args(["context", "del", "db"])
        assert args.context_cmd == "del"
        assert args.key == "db"

    def test_context_append_command(self, parser):
        args = parser.parse_args(["context", "append", "notes", "line2", "--by", "bob"])
        assert args.context_cmd == "append"
        assert args.key == "notes"
        assert args.value == "line2"

    def test_task_add_command(self, parser):
        args = parser.parse_args(["task", "add", "Build API", "--assign", "bob",
                                  "--priority", "high", "--by", "alice"])
        assert args.task_cmd == "add"
        assert args.title == "Build API"
        assert args.assign == "bob"
        assert args.priority == "high"

    def test_task_list_command(self, parser):
        args = parser.parse_args(["task", "list", "--status", "done", "--assigned", "alice"])
        assert args.task_cmd == "list"
        assert args.status == "done"
        assert args.assigned == "alice"

    def test_task_claim_command(self, parser):
        args = parser.parse_args(["task", "claim", "bob", "5"])
        assert args.task_cmd == "claim"
        assert args.name == "bob"
        assert args.task_id == 5

    def test_task_update_command(self, parser):
        args = parser.parse_args(["task", "update", "3", "done", "--result", "All good",
                                  "--by", "alice"])
        assert args.task_cmd == "update"
        assert args.task_id == 3
        assert args.new_status == "done"
        assert args.result == "All good"

    def test_task_show_command(self, parser):
        args = parser.parse_args(["task", "show", "7"])
        assert args.task_cmd == "show"
        assert args.task_id == 7

    def test_lock_command(self, parser):
        args = parser.parse_args(["lock", "alice", "main.py"])
        assert args.command == "lock"
        assert args.name == "alice"
        assert args.file == "main.py"

    def test_unlock_command(self, parser):
        args = parser.parse_args(["unlock", "alice", "main.py"])
        assert args.command == "unlock"

    def test_locks_command(self, parser):
        args = parser.parse_args(["locks"])
        assert args.command == "locks"

    def test_pending_command(self, parser):
        args = parser.parse_args(["pending", "alice"])
        assert args.command == "pending"
        assert args.name == "alice"

    def test_poll_command(self, parser):
        args = parser.parse_args(["poll", "alice"])
        assert args.command == "poll"
        assert args.name == "alice"

    def test_log_command(self, parser):
        args = parser.parse_args(["log", "--limit", "50"])
        assert args.command == "log"
        assert args.limit == 50

    def test_request_command(self, parser):
        args = parser.parse_args(["request", "alice", "bob", "Review PR"])
        assert args.command == "request"
        assert args.from_node == "alice"
        assert args.to == "bob"
        assert args.description == "Review PR"

    def test_inject_command(self, parser):
        args = parser.parse_args(["inject", "dev1", "poll dev1"])
        assert args.command == "inject"
        assert args.target == "dev1"
        assert args.prompt == "poll dev1"

    def test_interrupt_command(self, parser):
        args = parser.parse_args(["interrupt", "dev1"])
        assert args.command == "interrupt"
        assert args.target == "dev1"

    def test_nudge_command(self, parser):
        args = parser.parse_args(["nudge", "dev1", "Check tasks"])
        assert args.command == "nudge"
        assert args.target == "dev1"
        assert args.message == "Check tasks"

    def test_nudge_command_no_message(self, parser):
        args = parser.parse_args(["nudge", "dev1"])
        assert args.message == ""

    def test_windows_command(self, parser):
        args = parser.parse_args(["windows"])
        assert args.command == "windows"

    def test_whoami_command(self, parser):
        args = parser.parse_args(["whoami", "alice"])
        assert args.command == "whoami"
        assert args.name == "alice"

    def test_reset_command(self, parser):
        args = parser.parse_args(["reset", "--confirm"])
        assert args.command == "reset"
        assert args.confirm is True

    def test_state_dir_override(self, parser):
        args = parser.parse_args(["--state-dir", "/tmp/custom", "status"])
        assert args.state_dir == "/tmp/custom"

    def test_version(self, parser):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--version"])
        assert exc.value.code == 0


# ══════════════════════════════════════════════════════════════════
#  INTEGRATION / END-TO-END TESTS
# ══════════════════════════════════════════════════════════════════

class TestIntegration:

    def test_full_workflow(self, state, capture_stdout):
        """Simulate a complete collaboration workflow."""
        # Two nodes join
        collab.cmd_join(state, "arch", "architect")
        collab.cmd_join(state, "dev", "developer")

        # Architect creates tasks
        collab.cmd_task_add(state, "Design schema", assign="arch",
                            priority="high", by="arch")
        collab.cmd_task_add(state, "Implement API", assign="dev",
                            priority="high", by="arch")

        # Architect shares context
        collab.cmd_context_set(state, "db", "postgres", by="arch")

        # Dev polls and sees updates
        with capture_stdout() as cap:
            collab.cmd_poll(state, "dev")
        # Should see task assignment and context

        # Dev starts working
        collab.cmd_task_update(state, 2, "active", by="dev")
        collab.cmd_lock(state, "dev", "api.py")

        # Architect sends a message
        collab.cmd_send(state, "arch", "dev", "Don't forget auth middleware")

        # Dev checks pending
        with capture_stdout() as cap:
            collab.cmd_pending(state, "dev")

        # Dev finishes
        collab.cmd_task_update(state, 2, "done",
                               result_text="API endpoints implemented", by="dev")
        collab.cmd_unlock(state, "dev", "api.py")

        # Status shows everything
        with capture_stdout() as cap:
            collab.cmd_status(state)
        assert "2 node(s)" in cap.text

        # Verify final state
        tasks = state.read("tasks")
        assert tasks["2"]["status"] == "done"
        assert state.read("locks") == {}

    def test_broadcast_reaches_all(self, state, capture_stdout):
        """Broadcast should be visible to all other nodes."""
        collab.cmd_join(state, "a", "node-a")
        collab.cmd_join(state, "b", "node-b")
        collab.cmd_join(state, "c", "node-c")

        # Reset polls
        collab.cmd_poll(state, "a")
        collab.cmd_poll(state, "b")
        collab.cmd_poll(state, "c")

        time.sleep(0.02)  # ensure broadcast timestamp is strictly after last_poll
        collab.cmd_broadcast(state, "a", "System going down!")

        # b and c should see it
        with capture_stdout() as cap:
            collab.cmd_poll(state, "b")
        assert "System going down!" in cap.text

        with capture_stdout() as cap:
            collab.cmd_poll(state, "c")
        assert "System going down!" in cap.text

    def test_concurrent_state_updates(self, state):
        """Multiple rapid state updates shouldn't corrupt data."""
        collab.cmd_join(state, "a", "role-a")
        collab.cmd_join(state, "b", "role-b")

        for i in range(20):
            collab.cmd_context_set(state, f"key_{i}", f"value_{i}", by="a")

        ctx = state.read("context")
        assert len(ctx) == 20
        for i in range(20):
            assert ctx[f"key_{i}"]["value"] == f"value_{i}"

    def test_cli_main_dispatch(self, state_dir):
        """Test main() dispatches correctly via subprocess."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(collab.SCRIPT_PATH),
             "--state-dir", str(state_dir),
             "join", "testnode", "--role", "tester"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "[OK]" in result.stdout

    def test_cli_status_via_subprocess(self, state_dir):
        """Test status command via subprocess."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(collab.SCRIPT_PATH),
             "--state-dir", str(state_dir), "status"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "Collaboration Status" in result.stdout


# ══════════════════════════════════════════════════════════════════
#  WINDOW CONTROL TESTS (mocked — platform-independent)
# ══════════════════════════════════════════════════════════════════

class TestWindowControl:
    """Window control tests — mock the new inject.py-based API."""

    def test_cmd_windows_no_consoles(self, state, capture_stdout):
        with mock.patch("collab.list_all_sessions", return_value={}):
            collab.cmd_join(state, "alice", "test")
            with capture_stdout() as cap:
                collab.cmd_windows(state)
            assert "alice" in cap.text

    def test_cmd_inject_no_console(self, state):
        with mock.patch("collab.find_collab_window", return_value=""):
            with pytest.raises(SystemExit):
                collab.cmd_inject(state, "dev1", "hello")

    def test_cmd_interrupt_no_console(self, state):
        with mock.patch("collab.find_collab_window", return_value=""):
            with pytest.raises(SystemExit):
                collab.cmd_interrupt(state, "dev1")

    def test_cmd_nudge_no_console(self, populated_state, capture_stdout):
        with mock.patch("collab.find_collab_window", return_value=""):
            with capture_stdout() as cap:
                collab.cmd_nudge(populated_state, "bob", "Wake up")
            assert "WARN" in cap.text
            assert "signal" in cap.text.lower()
        # Should still have sent a message
        messages = populated_state.read("messages")
        assert any("Wake up" in m["content"] for m in messages)

    def test_cmd_nudge_with_console(self, populated_state, capture_stdout):
        with mock.patch("collab.find_collab_window", return_value="session-123"), \
             mock.patch("collab._run_inject", return_value=True):
            with capture_stdout() as cap:
                collab.cmd_nudge(populated_state, "bob", "Check tasks")
            assert "[OK]" in cap.text

    def test_cmd_inject_success(self, state, capture_stdout):
        with mock.patch("collab.find_collab_window", return_value="session-123"), \
             mock.patch("collab._run_inject", return_value=True):
            with capture_stdout() as cap:
                collab.cmd_inject(state, "dev1", "poll dev1")
            assert "[OK]" in cap.text

    def test_cmd_inject_failure(self, state, capture_stdout):
        with mock.patch("collab.find_collab_window", return_value="session-123"), \
             mock.patch("collab._run_inject", return_value=False):
            with pytest.raises(SystemExit):
                collab.cmd_inject(state, "dev1", "poll dev1")

    def test_cmd_interrupt_success(self, state, capture_stdout):
        with mock.patch("collab.find_collab_window", return_value="session-123"), \
             mock.patch("collab._run_interrupt", return_value=True):
            with capture_stdout() as cap:
                collab.cmd_interrupt(state, "dev1")
            assert "[OK]" in cap.text

    def test_cmd_interrupt_failure(self, state, capture_stdout):
        with mock.patch("collab.find_collab_window", return_value="session-123"), \
             mock.patch("collab._run_interrupt", return_value=False):
            with pytest.raises(SystemExit):
                collab.cmd_interrupt(state, "dev1")


# ══════════════════════════════════════════════════════════════════
#  EDGE CASE TESTS
# ══════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_special_characters_in_message(self, populated_state, capture_stdout):
        msg = 'Quote: "hello" & <tag> \\ newline\n end'
        collab.cmd_send(populated_state, "alice", "bob", msg)
        messages = populated_state.read("messages")
        assert messages[-1]["content"] == msg

    def test_unicode_in_context(self, state, capture_stdout):
        collab.cmd_context_set(state, "emoji", "Hello 🌍 世界", by="alice")
        ctx = state.read("context")
        assert ctx["emoji"]["value"] == "Hello 🌍 世界"

    def test_empty_string_values(self, state, capture_stdout):
        collab.cmd_context_set(state, "empty", "", by="alice")
        ctx = state.read("context")
        assert ctx["empty"]["value"] == ""

    def test_very_long_task_title(self, state, capture_stdout):
        long_title = "x" * 500
        with capture_stdout() as cap:
            collab.cmd_task_add(state, long_title, by="alice")
        tasks = state.read("tasks")
        assert tasks["1"]["title"] == long_title

    def test_rapid_task_id_generation(self, state):
        ids = [state.next_task_id() for _ in range(50)]
        assert ids == list(range(1, 51))
        assert len(set(ids)) == 50  # all unique

    def test_node_names_with_special_chars(self, state, capture_stdout):
        collab.cmd_join(state, "node-1", "test")
        collab.cmd_join(state, "node_2", "test")
        nodes = state.read("nodes")
        assert "node-1" in nodes
        assert "node_2" in nodes

    def test_multiple_locks_same_node(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "file1.py")
        collab.cmd_lock(populated_state, "alice", "file2.py")
        collab.cmd_lock(populated_state, "alice", "file3.py")
        locks = populated_state.read("locks")
        assert len(locks) == 3
        for f in ("file1.py", "file2.py", "file3.py"):
            assert locks[f]["held_by"] == "alice"

    def test_leave_releases_multiple_locks(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "a.py")
        collab.cmd_lock(populated_state, "alice", "b.py")
        with capture_stdout() as cap:
            collab.cmd_leave(populated_state, "alice")
        assert "Released 2 file lock" in cap.text
        assert populated_state.read("locks") == {}


# ══════════════════════════════════════════════════════════════════
#  V2.0 FEATURE TESTS
# ══════════════════════════════════════════════════════════════════

class TestTaskDependencies:

    def test_task_add_with_dependencies(self, state, capture_stdout):
        collab.cmd_task_add(state, "Task A", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_add(state, "Task B", depends_on="1", by="alice")
        assert "depends on" in cap.text
        tasks = state.read("tasks")
        assert tasks["2"]["depends_on"] == [1]

    def test_task_add_multiple_dependencies(self, state, capture_stdout):
        collab.cmd_task_add(state, "Task A", by="alice")
        collab.cmd_task_add(state, "Task B", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_add(state, "Task C", depends_on="1,2", by="alice")
        tasks = state.read("tasks")
        assert tasks["3"]["depends_on"] == [1, 2]

    def test_task_add_no_dependencies(self, state, capture_stdout):
        collab.cmd_task_add(state, "Task A", by="alice")
        tasks = state.read("tasks")
        assert tasks["1"]["depends_on"] == []

    def test_task_show_dependencies(self, state, capture_stdout):
        collab.cmd_task_add(state, "Dep task", by="alice")
        collab.cmd_task_add(state, "Main task", depends_on="1", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_show(state, 2)
        assert "Depends on" in cap.text
        assert "#1" in cap.text

    def test_task_list_shows_dependency_tags(self, state, capture_stdout):
        collab.cmd_task_add(state, "Base task", by="alice")
        collab.cmd_task_add(state, "Dependent", depends_on="1", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_list(state)
        assert "needs" in cap.text.lower() or "#1" in cap.text


class TestTaskComments:

    def test_add_comment(self, state, capture_stdout):
        collab.cmd_task_add(state, "Task A", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_comment(state, 1, "Looks good so far", by="bob")
        assert "[OK]" in cap.text
        tasks = state.read("tasks")
        assert len(tasks["1"]["comments"]) == 1
        assert tasks["1"]["comments"][0]["text"] == "Looks good so far"
        assert tasks["1"]["comments"][0]["by"] == "bob"

    def test_multiple_comments(self, state, capture_stdout):
        collab.cmd_task_add(state, "Task A", by="alice")
        collab.cmd_task_comment(state, 1, "Comment 1", by="alice")
        collab.cmd_task_comment(state, 1, "Comment 2", by="bob")
        collab.cmd_task_comment(state, 1, "Comment 3", by="alice")
        tasks = state.read("tasks")
        assert len(tasks["1"]["comments"]) == 3

    def test_comment_not_found(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_task_comment(state, 999, "Ghost", by="alice")

    def test_comment_shown_in_task_show(self, state, capture_stdout):
        collab.cmd_task_add(state, "Task A", by="alice")
        collab.cmd_task_comment(state, 1, "Review note", by="bob")
        with capture_stdout() as cap:
            collab.cmd_task_show(state, 1)
        assert "Review note" in cap.text
        assert "bob" in cap.text


class TestTaskReassign:

    def test_reassign(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Task A", assign="alice", by="alice")
        with capture_stdout() as cap:
            collab.cmd_task_reassign(populated_state, 1, "bob", by="alice")
        assert "[OK]" in cap.text
        assert "bob" in cap.text
        tasks = populated_state.read("tasks")
        assert tasks["1"]["assigned_to"] == "bob"

    def test_reassign_resets_active_to_claimed(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Task A", assign="alice", by="alice")
        collab.cmd_task_update(populated_state, 1, "active", by="alice")
        collab.cmd_task_reassign(populated_state, 1, "bob", by="alice")
        tasks = populated_state.read("tasks")
        assert tasks["1"]["status"] == "claimed"

    def test_reassign_signals_new_assignee(self, populated_state):
        collab.cmd_task_add(populated_state, "Task A", assign="alice", by="alice")
        collab.cmd_task_reassign(populated_state, 1, "bob", by="alice")
        signals = collab.read_and_clear_signal(populated_state.dir, "bob")
        assert any("reassigned" in s.lower() or "Task #1" in s for s in signals)

    def test_reassign_not_found(self, state):
        with pytest.raises(SystemExit):
            collab.cmd_task_reassign(state, 999, "bob", by="alice")

    def test_reassign_history(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Task A", assign="alice", by="lead")
        collab.cmd_task_reassign(populated_state, 1, "bob", by="lead")
        tasks = populated_state.read("tasks")
        history = tasks["1"]["history"]
        assert any("reassigned" in h["action"] for h in history)


class TestCommandAliases:

    def test_expand_status_alias(self):
        result = collab._expand_aliases(["s"])
        assert result == ["status"]

    def test_expand_poll_alias(self):
        result = collab._expand_aliases(["p", "alice"])
        assert result == ["poll", "alice"]

    def test_expand_task_alias(self):
        result = collab._expand_aliases(["t", "list"])
        assert result == ["task", "list"]

    def test_expand_broadcast_alias(self):
        result = collab._expand_aliases(["b", "alice", "Hello!"])
        assert result == ["broadcast", "alice", "Hello!"]

    def test_no_alias_expansion_for_unknown(self):
        result = collab._expand_aliases(["status"])
        assert result == ["status"]

    def test_flag_value_not_expanded(self):
        # _expand_aliases treats the first non-flag token as the command.
        # With "--state-dir /tmp s", /tmp is seen as the command (not an alias),
        # so "s" is never reached. This is fine — argparse handles the rest.
        result = collab._expand_aliases(["--state-dir", "/tmp", "s"])
        assert result == ["--state-dir", "/tmp", "s"]  # /tmp isn't an alias, no expansion

    def test_alias_as_first_arg(self):
        # The common case: alias is the very first arg
        result = collab._expand_aliases(["s"])
        assert result == ["status"]

    def test_empty_argv(self):
        result = collab._expand_aliases([])
        assert result == []

    def test_all_aliases_exist(self):
        """Every alias should map to a valid command."""
        parser = collab.build_parser()
        for alias, cmd in collab.ALIASES.items():
            # Verify the alias target is a valid subcommand
            assert cmd in parser._subparsers._group_actions[0].choices, \
                f"Alias '{alias}' maps to unknown command '{cmd}'"


class TestHealthCommand:

    def test_health_empty(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_health(state)
        assert "No nodes registered" in cap.text

    def test_health_with_nodes(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_health(populated_state)
        assert "Node Health" in cap.text
        assert "alice" in cap.text
        assert "bob" in cap.text
        assert "OK" in cap.text

    def test_health_shows_task_counts(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Task A", assign="alice", by="alice")
        collab.cmd_task_update(populated_state, 1, "active", by="alice")
        with capture_stdout() as cap:
            collab.cmd_health(populated_state)
        assert "1 active" in cap.text


class TestSummaryCommand:

    def test_summary_empty(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_summary(state)
        assert "Session Summary" in cap.text
        assert "0 total" in cap.text

    def test_summary_with_work(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Task A", assign="alice", by="alice")
        collab.cmd_task_update(populated_state, 1, "done",
                               result_text="Complete", by="alice")
        collab.cmd_task_add(populated_state, "Task B", assign="bob", by="alice")
        collab.cmd_send(populated_state, "alice", "bob", "Hello")
        with capture_stdout() as cap:
            collab.cmd_summary(populated_state)
        assert "Session Summary" in cap.text
        assert "1 done" in cap.text
        assert "1 in progress" in cap.text
        assert "Completed Work" in cap.text
        assert "Messages:" in cap.text

    def test_summary_per_node_breakdown(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Task A", assign="alice", by="lead")
        collab.cmd_task_update(populated_state, 1, "done", by="alice")
        collab.cmd_task_add(populated_state, "Task B", assign="bob", by="lead")
        collab.cmd_task_update(populated_state, 2, "done", by="bob")
        with capture_stdout() as cap:
            collab.cmd_summary(populated_state)
        assert "Per-Node Breakdown" in cap.text
        assert "alice" in cap.text
        assert "bob" in cap.text


class TestTaskSorting:

    def test_tasks_sorted_by_status_then_priority(self, state, capture_stdout):
        collab.cmd_task_add(state, "Low open", priority="low", by="a")
        collab.cmd_task_add(state, "High open", priority="high", by="a")
        collab.cmd_task_add(state, "Active task", priority="medium", by="a")
        collab.cmd_task_update(state, 3, "active", by="a")
        collab.cmd_task_add(state, "Done task", by="a")
        collab.cmd_task_update(state, 4, "done", by="a")
        with capture_stdout() as cap:
            collab.cmd_task_list(state)
        lines = cap.text.strip().split("\n")
        # Active should come first, then open (high before low), then done
        task_lines = [l for l in lines if l.strip().startswith("#")]
        assert "Active task" in task_lines[0]
        # Done should be last
        assert "Done task" in task_lines[-1]


class TestLockExpiry:

    def test_file_lock_expiry_constant(self):
        assert collab.FILE_LOCK_EXPIRY == 1800  # 30 minutes

    def test_stale_node_constant(self):
        assert collab.STALE_NODE_SEC == 300  # 5 minutes


class TestCLIParserV2:
    """Tests for v2.0 CLI parser additions."""

    @pytest.fixture
    def parser(self):
        return collab.build_parser()

    def test_task_add_depends_on(self, parser):
        args = parser.parse_args(["task", "add", "Build API", "--depends-on", "1,3"])
        assert args.depends_on == "1,3"

    def test_task_comment_command(self, parser):
        args = parser.parse_args(["task", "comment", "5", "Looks good", "--by", "alice"])
        assert args.task_cmd == "comment"
        assert args.task_id == 5
        assert args.text == "Looks good"
        assert args.by == "alice"

    def test_task_reassign_command(self, parser):
        args = parser.parse_args(["task", "reassign", "3", "bob", "--by", "alice"])
        assert args.task_cmd == "reassign"
        assert args.task_id == 3
        assert args.new_assignee == "bob"

    def test_health_command(self, parser):
        args = parser.parse_args(["health"])
        assert args.command == "health"

    def test_summary_command(self, parser):
        args = parser.parse_args(["summary"])
        assert args.command == "summary"

    def test_status_compact_flag(self, parser):
        args = parser.parse_args(["status", "--compact"])
        assert args.command == "status"
        assert args.compact is True

    def test_status_default_no_compact(self, parser):
        args = parser.parse_args(["status"])
        assert args.compact is False


# ══════════════════════════════════════════════════════════════════
#  COMPACT STATUS TESTS
# ══════════════════════════════════════════════════════════════════

class TestCompactStatus:

    def test_compact_empty(self, state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_status(state, compact=True)
        assert "[status]" in cap.text
        assert "0 nodes" in cap.text

    def test_compact_with_nodes(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_status(populated_state, compact=True)
        assert "[status]" in cap.text
        assert "2 nodes" in cap.text
        assert "nodes:" in cap.text
        assert "alice" in cap.text
        assert "bob" in cap.text

    def test_compact_with_tasks(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Active task", assign="alice", by="lead")
        collab.cmd_task_update(populated_state, 1, "active", by="alice")
        with capture_stdout() as cap:
            collab.cmd_status(populated_state, compact=True)
        assert "tasks:" in cap.text
        assert "#1" in cap.text

    def test_compact_with_locks(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "main.py")
        with capture_stdout() as cap:
            collab.cmd_status(populated_state, compact=True)
        assert "locks:" in cap.text
        assert "main.py" in cap.text
        assert "alice" in cap.text

    def test_compact_hides_done_tasks(self, populated_state, capture_stdout):
        collab.cmd_task_add(populated_state, "Done task", assign="alice", by="lead")
        collab.cmd_task_update(populated_state, 1, "done", by="alice")
        with capture_stdout() as cap:
            collab.cmd_status(populated_state, compact=True)
        # Done tasks should not appear in compact task line
        assert "Done task" not in cap.text

    def test_full_status_still_works(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            collab.cmd_status(populated_state, compact=False)
        assert "=== Collaboration Status ===" in cap.text


# ══════════════════════════════════════════════════════════════════
#  IMPROVED ERROR MESSAGE TESTS
# ══════════════════════════════════════════════════════════════════

class TestImprovedErrors:

    def test_send_shows_active_nodes(self, populated_state, capture_stdout):
        try:
            collab.cmd_send(populated_state, "alice", "ghost", "hi")
        except SystemExit:
            pass
        # Re-run capturing output
        with capture_stdout() as cap:
            try:
                collab.cmd_send(populated_state, "alice", "ghost", "hi")
            except SystemExit:
                pass
        assert "Active nodes:" in cap.text
        assert "alice" in cap.text

    def test_lock_conflict_suggests_action(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "test.py")
        with capture_stdout() as cap:
            try:
                collab.cmd_lock(populated_state, "bob", "test.py")
            except SystemExit:
                pass
        assert "alice" in cap.text
        assert "send" in cap.text.lower() or "wait" in cap.text.lower()

    def test_unlock_other_explains_expiry(self, populated_state, capture_stdout):
        collab.cmd_lock(populated_state, "alice", "test.py")
        with capture_stdout() as cap:
            try:
                collab.cmd_unlock(populated_state, "bob", "test.py")
            except SystemExit:
                pass
        assert "alice" in cap.text
        assert "auto-expire" in cap.text or "30m" in cap.text

    def test_heartbeat_suggests_join(self, state, capture_stdout):
        with capture_stdout() as cap:
            try:
                collab.cmd_heartbeat(state, "ghost")
            except SystemExit:
                pass
        assert "join" in cap.text.lower()

    def test_task_not_found_suggests_list(self, state, capture_stdout):
        with capture_stdout() as cap:
            try:
                collab.cmd_task_show(state, 999)
            except SystemExit:
                pass
        assert "task list" in cap.text

    def test_context_not_found_shows_keys(self, state, capture_stdout):
        collab.cmd_context_set(state, "db", "pg", by="a")
        with capture_stdout() as cap:
            try:
                collab.cmd_context_get(state, "nonexistent")
            except SystemExit:
                pass
        assert "Available keys:" in cap.text
        assert "db" in cap.text

    def test_request_shows_active_nodes(self, populated_state, capture_stdout):
        with capture_stdout() as cap:
            try:
                collab.cmd_request(populated_state, "alice", "ghost", "help")
            except SystemExit:
                pass
        assert "Active nodes:" in cap.text


# ══════════════════════════════════════════════════════════════════
#  AUTO-HEARTBEAT TESTS
# ══════════════════════════════════════════════════════════════════

class TestAutoHeartbeat:
    """Verify that commands automatically update the node's heartbeat."""

    def test_send_updates_heartbeat(self, populated_state):
        # Set heartbeat to old timestamp
        def _age(nodes):
            nodes["alice"]["last_heartbeat"] = "2020-01-01T00:00:00+00:00"
        populated_state.update("nodes", _age)
        collab.cmd_send(populated_state, "alice", "bob", "test")
        nodes = populated_state.read("nodes")
        assert nodes["alice"]["last_heartbeat"] > "2025-01-01"

    def test_broadcast_updates_heartbeat(self, populated_state):
        def _age(nodes):
            nodes["alice"]["last_heartbeat"] = "2020-01-01T00:00:00+00:00"
        populated_state.update("nodes", _age)
        collab.cmd_broadcast(populated_state, "alice", "test")
        nodes = populated_state.read("nodes")
        assert nodes["alice"]["last_heartbeat"] > "2025-01-01"

    def test_lock_updates_heartbeat(self, populated_state):
        def _age(nodes):
            nodes["alice"]["last_heartbeat"] = "2020-01-01T00:00:00+00:00"
        populated_state.update("nodes", _age)
        collab.cmd_lock(populated_state, "alice", "file.py")
        nodes = populated_state.read("nodes")
        assert nodes["alice"]["last_heartbeat"] > "2025-01-01"

    def test_task_update_updates_heartbeat(self, populated_state):
        collab.cmd_task_add(populated_state, "Test task", by="alice")
        def _age(nodes):
            nodes["alice"]["last_heartbeat"] = "2020-01-01T00:00:00+00:00"
        populated_state.update("nodes", _age)
        collab.cmd_task_update(populated_state, 1, "active", by="alice")
        nodes = populated_state.read("nodes")
        assert nodes["alice"]["last_heartbeat"] > "2025-01-01"

    def test_pending_updates_heartbeat(self, populated_state):
        def _age(nodes):
            nodes["alice"]["last_heartbeat"] = "2020-01-01T00:00:00+00:00"
        populated_state.update("nodes", _age)
        collab.cmd_pending(populated_state, "alice")
        nodes = populated_state.read("nodes")
        assert nodes["alice"]["last_heartbeat"] > "2025-01-01"

    def test_system_by_skips_heartbeat(self, state):
        """Commands with by='system' should not try to update heartbeat."""
        # This should not crash even though 'system' is not a registered node
        collab.cmd_context_set(state, "key", "val", by="system")
        ctx = state.read("context")
        assert ctx["key"]["value"] == "val"


# ══════════════════════════════════════════════════════════════
#  CLAUDE.MD INJECTION & CLEANUP
# ══════════════════════════════════════════════════════════════


class TestClaudeMdSetup:
    """Tests for setup_claude_md in launcher.py — injection of collab instructions."""

    def test_creates_new_claude_md(self, tmp_path):
        """setup_claude_md creates CLAUDE.md when it doesn't exist."""
        import launcher
        launcher.setup_claude_md(tmp_path)
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert launcher.COLLAB_MARKER in content
        assert "## Multi-Instance Collaboration" in content

    def test_injects_markers_correctly(self, tmp_path):
        """Injected section is wrapped in matching COLLAB_MARKER pairs."""
        import launcher
        launcher.setup_claude_md(tmp_path)
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        markers = content.count(launcher.COLLAB_MARKER)
        assert markers == 2, f"Expected 2 markers, found {markers}"
        # First marker should come before the last one
        first = content.index(launcher.COLLAB_MARKER)
        last = content.rindex(launcher.COLLAB_MARKER)
        assert first < last

    def test_preserves_existing_content(self, tmp_path):
        """Existing CLAUDE.md content outside markers is preserved."""
        import launcher
        claude_md = tmp_path / "CLAUDE.md"
        original = "# My Project\n\nCustom instructions here.\n"
        claude_md.write_text(original, encoding="utf-8")
        launcher.setup_claude_md(tmp_path)
        content = claude_md.read_text(encoding="utf-8")
        assert "# My Project" in content
        assert "Custom instructions here." in content
        assert launcher.COLLAB_MARKER in content

    def test_replaces_existing_section(self, tmp_path):
        """Running setup_claude_md twice replaces rather than duplicating."""
        import launcher
        launcher.setup_claude_md(tmp_path, num_nodes=3)
        launcher.setup_claude_md(tmp_path, num_nodes=5)
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        # Should still have exactly 2 markers (one opening, one closing)
        assert content.count(launcher.COLLAB_MARKER) == 2
        # Should reflect the newer num_nodes
        assert "1 of 5" in content

    def test_lite_tier(self, tmp_path):
        """Lite tier uses the simplified protocol section."""
        import launcher
        launcher.setup_claude_md(tmp_path, tier="lite")
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert launcher.COLLAB_MARKER in content
        # Lite section should still have the marker pair
        assert content.count(launcher.COLLAB_MARKER) == 2

    def test_full_tier_includes_lead_instructions(self, tmp_path):
        """Full tier includes lead behavior and terminal control docs."""
        import launcher
        launcher.setup_claude_md(tmp_path, tier="full")
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Lead" in content
        assert "inject" in content.lower()

    def test_preserves_content_before_and_after_marker(self, tmp_path):
        """Content both before and after the injection point is preserved."""
        import launcher
        claude_md = tmp_path / "CLAUDE.md"
        original = "# Header\n\nBefore content.\n\n## Footer\n\nAfter content.\n"
        claude_md.write_text(original, encoding="utf-8")
        launcher.setup_claude_md(tmp_path)
        content = claude_md.read_text(encoding="utf-8")
        # Original content should appear before the markers (appended at end)
        assert "Before content." in content
        assert "After content." in content


class TestClaudeMdCleanup:
    """Tests for cmd_cleanup in collab.py — removal of collab instructions."""

    def test_cleanup_removes_injected_section(self, state, tmp_path):
        """cleanup removes the auto-generated collab section."""
        import launcher
        launcher.setup_claude_md(tmp_path)
        collab.cmd_cleanup(state, project_dir=str(tmp_path))
        claude_md = tmp_path / "CLAUDE.md"
        # File was entirely collab section — should be deleted
        assert not claude_md.exists()

    def test_cleanup_preserves_original_content(self, state, tmp_path):
        """cleanup keeps user content outside the markers."""
        import launcher
        claude_md = tmp_path / "CLAUDE.md"
        original_lines = "# My Project\n\nThis is my custom config.\n"
        claude_md.write_text(original_lines, encoding="utf-8")
        launcher.setup_claude_md(tmp_path)
        # Verify injection happened
        assert launcher.COLLAB_MARKER in claude_md.read_text(encoding="utf-8")
        # Run cleanup
        collab.cmd_cleanup(state, project_dir=str(tmp_path))
        cleaned = claude_md.read_text(encoding="utf-8")
        assert launcher.COLLAB_MARKER not in cleaned
        assert "# My Project" in cleaned
        assert "This is my custom config." in cleaned

    def test_cleanup_no_claude_md(self, state, tmp_path, capture_stdout):
        """cleanup handles missing CLAUDE.md gracefully."""
        with capture_stdout() as cap:
            collab.cmd_cleanup(state, project_dir=str(tmp_path))
        assert "nothing to clean up" in cap.text.lower()

    def test_cleanup_no_markers(self, state, tmp_path, capture_stdout):
        """cleanup handles CLAUDE.md without markers gracefully."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Just a normal file\n", encoding="utf-8")
        with capture_stdout() as cap:
            collab.cmd_cleanup(state, project_dir=str(tmp_path))
        assert "nothing to clean up" in cap.text.lower()
        # File should be untouched
        assert claude_md.read_text(encoding="utf-8") == "# Just a normal file\n"

    def test_cleanup_defaults_to_cwd(self, state, tmp_path):
        """cleanup uses cwd when no project_dir specified."""
        import launcher
        launcher.setup_claude_md(tmp_path)
        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            # Use Path.cwd mock since cmd_cleanup uses Path.cwd()
            with mock.patch.object(Path, "cwd", return_value=tmp_path):
                collab.cmd_cleanup(state, project_dir="")
        # Should have cleaned up since markers were present
        claude_md = tmp_path / "CLAUDE.md"
        assert not claude_md.exists()

    def test_roundtrip_inject_then_cleanup(self, state, tmp_path):
        """Full roundtrip: original → inject → cleanup → original restored."""
        import launcher
        claude_md = tmp_path / "CLAUDE.md"
        original = "# Project\n\n## Rules\n\n- Rule 1\n- Rule 2\n"
        claude_md.write_text(original, encoding="utf-8")

        # Inject
        launcher.setup_claude_md(tmp_path, num_nodes=4)
        injected = claude_md.read_text(encoding="utf-8")
        assert launcher.COLLAB_MARKER in injected
        assert "1 of 4" in injected

        # Cleanup
        collab.cmd_cleanup(state, project_dir=str(tmp_path))
        restored = claude_md.read_text(encoding="utf-8")

        # Original content should be fully restored
        assert "# Project" in restored
        assert "- Rule 1" in restored
        assert "- Rule 2" in restored
        assert launcher.COLLAB_MARKER not in restored

    def test_cleanup_after_double_inject(self, state, tmp_path):
        """cleanup works correctly after two successive injections."""
        import launcher
        claude_md = tmp_path / "CLAUDE.md"
        original = "# Original\n"
        claude_md.write_text(original, encoding="utf-8")

        launcher.setup_claude_md(tmp_path, num_nodes=3)
        launcher.setup_claude_md(tmp_path, num_nodes=5)
        # Should still only have 2 markers
        assert claude_md.read_text(encoding="utf-8").count(launcher.COLLAB_MARKER) == 2

        collab.cmd_cleanup(state, project_dir=str(tmp_path))
        restored = claude_md.read_text(encoding="utf-8")
        assert "# Original" in restored
        assert launcher.COLLAB_MARKER not in restored
