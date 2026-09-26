"""Integration tests for SessionArchive wiring in Orchestrator."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.session_archive import SessionArchive


@pytest.fixture
def archive(tmp_path: Path) -> SessionArchive:
    return SessionArchive(db_path=tmp_path / "sessions.db")


def _make_orchestrator(archive: SessionArchive):
    """Construct an orchestrator with all heavy deps stubbed out."""
    from core.orchestrator import Orchestrator

    with patch("core.orchestrator.anthropic.Anthropic"), \
         patch("core.orchestrator.SkillLoader") as sl_cls, \
         patch("core.orchestrator.SkillSelector") as ss_cls, \
         patch("core.orchestrator.ContainerManager") as cm_cls, \
         patch("core.orchestrator.MemoryProvider") as mp_cls, \
         patch("core.orchestrator.PromptBuilder") as pb_cls, \
         patch("core.orchestrator.ToolLoop") as tl_cls:
        sl_cls.return_value.load_all.return_value = {}
        sl_cls.return_value.skipped_skills = {}
        sl_cls.return_value.invalid_skills = {}
        ss_cls.return_value.available = False
        pb_cls.return_value.build.return_value = "system"
        pb_cls.return_value.build_cacheable_parts.return_value = ("system", "")
        tl_cls.return_value.run.return_value = "response text"

        orch = Orchestrator(anthropic_api_key="test", archive=archive)
        return orch, tl_cls.return_value


def test_start_session_creates_session_row(archive: SessionArchive):
    orch, _ = _make_orchestrator(archive)
    orch.start_session("text")
    assert orch._current_session_id > 0


def test_end_session_finalizes_and_resets(archive: SessionArchive):
    orch, _ = _make_orchestrator(archive)
    orch.start_session("text")
    sid = orch._current_session_id
    orch.end_session()
    assert orch._current_session_id is None

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        ended = conn.execute(
            "SELECT ended_at FROM sessions WHERE id = ?", (sid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert ended is not None


def test_end_session_without_start_is_noop(archive: SessionArchive):
    orch, _ = _make_orchestrator(archive)
    orch.end_session()  # must not raise


def test_process_message_archives_user_and_assistant(archive: SessionArchive):
    orch, tl = _make_orchestrator(archive)

    def fake_run(user_message, system_prompt, archive_callback=None, on_chunk=None, **kwargs):
        if archive_callback:
            archive_callback(user_message, [], "the assistant reply")
        return "the assistant reply"

    tl.run.side_effect = fake_run
    orch.start_session("text")
    orch.process_message("hello there")

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        rows = conn.execute(
            "SELECT role, content FROM turns WHERE session_id = ? ORDER BY turn_index",
            (orch._current_session_id,),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("user", "hello there"), ("assistant", "the assistant reply")]


def test_process_message_archives_tool_activity(archive: SessionArchive):
    orch, tl = _make_orchestrator(archive)

    def fake_run(user_message, system_prompt, archive_callback=None, on_chunk=None, **kwargs):
        if archive_callback:
            tool_activity = [{
                "name": "weather",
                "input": {"city": "Paris"},
                "result": "Paris: 14C, light rain",
            }]
            archive_callback(user_message, tool_activity, "It is 14 in Paris.")
        return "It is 14 in Paris."

    tl.run.side_effect = fake_run
    orch.start_session("text")
    orch.process_message("weather in Paris")

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        rows = conn.execute(
            "SELECT role, content, tool_name FROM turns "
            "WHERE session_id = ? ORDER BY turn_index",
            (orch._current_session_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    assert rows[0] == ("user", "weather in Paris", None)
    assert rows[1][0] == "tool"
    assert rows[1][2] == "weather"
    assert "Paris" in rows[1][1]
    assert rows[2] == ("assistant", "It is 14 in Paris.", None)


def test_archive_noop_without_started_session(archive: SessionArchive):
    """If no session has been started, archive_callback writes nothing but does not raise."""
    orch, tl = _make_orchestrator(archive)

    def fake_run(user_message, system_prompt, archive_callback=None, on_chunk=None, **kwargs):
        if archive_callback:
            archive_callback(user_message, [], "reply")
        return "reply"

    tl.run.side_effect = fake_run
    orch.process_message("no session")  # no start_session called

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_record_local_turn_updates_history_and_archive(archive: SessionArchive):
    orch, tool_loop = _make_orchestrator(archive)
    orch.start_session("voice")
    orch.record_local_turn("what's your name", "I'm Jarvis.")

    assert orch.conversation_state.messages[-2] == {"role": "user", "content": "what's your name"}
    assert orch.conversation_state.messages[-1]["content"] == [{"type": "text", "text": "I'm Jarvis."}]
    tool_loop.run.assert_not_called()

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        rows = conn.execute(
            "SELECT role, content FROM turns WHERE session_id = ? ORDER BY id",
            (orch._current_session_id,),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("user", "what's your name"), ("assistant", "I'm Jarvis.")]


def test_intent_hint_goes_to_uncached_dynamic_block(archive: SessionArchive):
    orch, tool_loop = _make_orchestrator(archive)
    orch._tier_router = None
    orch.process_message("to the weather today", intent_hint="HINT")
    kwargs = tool_loop.run.call_args.kwargs
    assert kwargs["system_prompt"] == "system"          # cached prefix untouched
    assert kwargs["system_prompt_dynamic"].endswith("HINT")  # hint on the uncached side


def test_no_intent_hint_adds_nothing_but_the_clock(archive: SessionArchive):
    orch, tool_loop = _make_orchestrator(archive)
    orch._tier_router = None
    orch.process_message("hello")
    assert tool_loop.run.call_args.kwargs["system_prompt_dynamic"].startswith("Current date and time:")


def test_live_date_with_year_on_uncached_side_every_turn(archive: SessionArchive):
    from datetime import datetime
    orch, tool_loop = _make_orchestrator(archive)
    orch._tier_router = None
    orch.inject_startup_context("Kaizen started on Friday, September 25, 2026 at 10:40 PM.")
    fixed = datetime(2026, 9, 27, 9, 5)
    with patch("core.orchestrator.datetime") as dt:
        dt.now.return_value = fixed
        orch.process_message("what's tomorrow's weather")
    kwargs = tool_loop.run.call_args.kwargs
    assert "Current date and time: Sunday, September 27, 2026, 9:05 AM." in kwargs["system_prompt_dynamic"]
    assert "Current date and time" not in kwargs["system_prompt"]
