"""
Unit tests for auric.memory.chronicles.

Tests cover:
- _append_to_file: file existence check, write behavior, return values
- _unescape_list: HTML entity unescaping
- perform_dream_cycle: all 4 steps with various code paths
  - Step 1: session summarization (idle, active, no session, error cases)
  - Step 2: daily log reading (missing, empty, populated)
  - Step 3: applying updates to MEMORY.md, USER.md, HEARTBEAT.md
  - Step 4: dream story generation (enabled, disabled, errors)
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(*, enable_dream_stories: bool = True) -> MagicMock:
    """Build a minimal mock config matching AgentsConfig structure."""
    config = MagicMock()
    config.agents.enable_dream_stories = enable_dream_stories
    config.agents.models = {
        "smart_model": SimpleNamespace(model="test-smart"),
        "fast_model": SimpleNamespace(model="test-fast"),
        "heartbeat_model": SimpleNamespace(model="test-heartbeat"),
    }
    return config


def _make_llm_response(content: str) -> MagicMock:
    """Build a mock LLM response with the given content."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    return response


def _make_gateway(llm_json: dict | None = None, dream_text: str = "") -> AsyncMock:
    """Build a mock gateway that returns JSON for smart_model and text for fast_model."""
    gateway = AsyncMock()

    default_json = {
        "cleaned_daily_log": "Cleaned log content.",
        "memory_updates": ["learned something"],
        "user_updates": ["user likes cats"],
        "heartbeat_updates": ["remind user at 3pm"],
    }

    responses = {
        "smart_model": _make_llm_response(json.dumps(llm_json or default_json)),
        "fast_model": _make_llm_response(dream_text or "I dreamt of electric sheep."),
    }

    async def _chat_completion(*, messages, tier, **kwargs):
        return responses.get(tier, responses["smart_model"])

    gateway.chat_completion = AsyncMock(side_effect=_chat_completion)
    return gateway


def _make_audit_logger(
    *,
    last_sid: str | None = "session-123",
    last_msg_time: datetime | None = None,
    history_empty: bool = False,
) -> AsyncMock:
    """Build a mock audit logger."""
    audit = AsyncMock()
    audit.get_last_active_session_id = AsyncMock(return_value=last_sid)

    if history_empty or last_sid is None:
        audit.get_chat_history = AsyncMock(return_value=[])
    else:
        msg = MagicMock()
        msg.timestamp = last_msg_time or (datetime.now() - timedelta(minutes=10))
        audit.get_chat_history = AsyncMock(return_value=[msg])

    audit.summarize_session = AsyncMock()
    return audit


# ---------------------------------------------------------------------------
# Tests: _unescape_list
# ---------------------------------------------------------------------------

class TestUnescapeList:

    def test_basic_entities(self):
        from auric.memory.chronicles import _unescape_list
        assert _unescape_list(["hello &amp; world"]) == ["hello & world"]

    def test_multiple_items(self):
        from auric.memory.chronicles import _unescape_list
        result = _unescape_list(["&lt;b&gt;", "a &amp; b", "plain"])
        assert result == ["<b>", "a & b", "plain"]

    def test_empty_list(self):
        from auric.memory.chronicles import _unescape_list
        assert _unescape_list([]) == []

    def test_numeric_entities(self):
        from auric.memory.chronicles import _unescape_list
        assert _unescape_list(["line1&#10;line2"]) == ["line1\nline2"]

    def test_no_entities(self):
        from auric.memory.chronicles import _unescape_list
        assert _unescape_list(["plain text"]) == ["plain text"]


# ---------------------------------------------------------------------------
# Tests: _append_to_file
# ---------------------------------------------------------------------------

class TestAppendToFile:

    @pytest.mark.asyncio
    async def test_appends_to_existing_file(self, tmp_path):
        from auric.memory.chronicles import _append_to_file
        target = tmp_path / "test.md"
        target.write_text("existing\n", encoding="utf-8")

        result = await _append_to_file(target, "appended\n")

        assert result is True
        assert target.read_text(encoding="utf-8") == "existing\nappended\n"

    @pytest.mark.asyncio
    async def test_returns_false_if_missing(self, tmp_path):
        from auric.memory.chronicles import _append_to_file
        target = tmp_path / "nonexistent.md"

        result = await _append_to_file(target, "data")

        assert result is False

    @pytest.mark.asyncio
    async def test_appends_empty_string(self, tmp_path):
        from auric.memory.chronicles import _append_to_file
        target = tmp_path / "test.md"
        target.write_text("original", encoding="utf-8")

        result = await _append_to_file(target, "")

        assert result is True
        assert target.read_text(encoding="utf-8") == "original"

    @pytest.mark.asyncio
    async def test_appends_unicode(self, tmp_path):
        from auric.memory.chronicles import _append_to_file
        target = tmp_path / "test.md"
        target.write_text("", encoding="utf-8")

        await _append_to_file(target, "💤 Dream 🌙\n")

        assert "💤" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests: perform_dream_cycle — Step 1 (Session Summarization)
# ---------------------------------------------------------------------------

class TestDreamCycleStep1:

    @pytest.mark.asyncio
    async def test_summarizes_idle_session(self, tmp_path):
        """Session idle >5m should trigger summarize_session."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger(last_msg_time=datetime.now() - timedelta(minutes=10))
        gateway = _make_gateway()
        config = _make_config()

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            await perform_dream_cycle(audit, gateway, config)

        audit.summarize_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_active_session(self, tmp_path):
        """Session active <5m should NOT trigger summarize_session."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger(last_msg_time=datetime.now() - timedelta(minutes=1))
        gateway = _make_gateway()
        config = _make_config()

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            await perform_dream_cycle(audit, gateway, config)

        audit.summarize_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_active_session(self, tmp_path):
        """No last active session should skip summarization entirely."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config()

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            await perform_dream_cycle(audit, gateway, config)

        audit.summarize_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_history(self, tmp_path):
        """Session exists but no messages — should skip summarization."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger(history_empty=True)
        gateway = _make_gateway()
        config = _make_config()

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            await perform_dream_cycle(audit, gateway, config)

        audit.summarize_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_summarize_error_is_caught(self, tmp_path):
        """summarize_session error should be logged, not raised."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger()
        audit.summarize_session = AsyncMock(side_effect=RuntimeError("db exploded"))
        gateway = _make_gateway()
        config = _make_config()

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            # Should not raise
            await perform_dream_cycle(audit, gateway, config)

    @pytest.mark.asyncio
    async def test_uses_heartbeat_model(self, tmp_path):
        """Should prefer heartbeat_model for session summarization."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger()
        gateway = _make_gateway()
        config = _make_config()

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            await perform_dream_cycle(audit, gateway, config)

        audit.summarize_session.assert_called_once_with(
            "session-123", gateway, model="test-heartbeat"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_fast_model(self, tmp_path):
        """Missing heartbeat_model should fall back to fast_model."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger()
        gateway = _make_gateway()
        config = _make_config()
        # Remove heartbeat_model — .get() returns None
        config.agents.models = {
            "smart_model": SimpleNamespace(model="test-smart"),
            "fast_model": SimpleNamespace(model="test-fast"),
        }

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            await perform_dream_cycle(audit, gateway, config)

        audit.summarize_session.assert_called_once_with(
            "session-123", gateway, model="test-fast"
        )


# ---------------------------------------------------------------------------
# Tests: perform_dream_cycle — Step 2 (Daily Log Reading)
# ---------------------------------------------------------------------------

class TestDreamCycleStep2:

    @pytest.mark.asyncio
    async def test_missing_daily_log_exits_early(self, tmp_path):
        """No daily log file → return early, no LLM call."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config()

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            (tmp_path / "memories").mkdir()
            # Don't create the daily log
            await perform_dream_cycle(audit, gateway, config)

        gateway.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_daily_log_exits_early(self, tmp_path):
        """Empty daily log → return early, no LLM call."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config()
        today = datetime.now().strftime("%Y-%m-%d")

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            mem_dir = tmp_path / "memories"
            mem_dir.mkdir()
            (mem_dir / f"{today}.md").write_text("   \n  \n", encoding="utf-8")

            await perform_dream_cycle(audit, gateway, config)

        gateway.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_log_exits_early(self, tmp_path):
        """Whitespace-only daily log → return early."""
        from auric.memory.chronicles import perform_dream_cycle

        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config()
        today = datetime.now().strftime("%Y-%m-%d")

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            mem_dir = tmp_path / "memories"
            mem_dir.mkdir()
            (mem_dir / f"{today}.md").write_text("\t\n \n", encoding="utf-8")

            await perform_dream_cycle(audit, gateway, config)

        gateway.chat_completion.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: perform_dream_cycle — Step 3 (Apply Updates)
# ---------------------------------------------------------------------------

class TestDreamCycleStep3:

    def _setup_auric_tree(self, tmp_path: Path):
        """Create the minimal .auric file tree for a full dream cycle."""
        mem_dir = tmp_path / "memories"
        mem_dir.mkdir(exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        (mem_dir / f"{today}.md").write_text("# Today\nSome events happened.", encoding="utf-8")
        (mem_dir / "MEMORY.md").write_text("## Facts\n- existing fact\n", encoding="utf-8")
        (tmp_path / "USER.md").write_text("# User Profile\n- name: Dan\n", encoding="utf-8")
        (tmp_path / "HEARTBEAT.md").write_text("# Heartbeat\n", encoding="utf-8")

        return today

    @pytest.mark.asyncio
    async def test_daily_log_overwritten_with_cleaned_version(self, tmp_path):
        """Cleaned log should overwrite the daily log with a Dream Cycle Complete marker."""
        from auric.memory.chronicles import perform_dream_cycle

        today = self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        daily = (tmp_path / "memories" / f"{today}.md").read_text(encoding="utf-8")
        assert "Cleaned log content." in daily
        assert "**Dream Cycle Complete.**" in daily

    @pytest.mark.asyncio
    async def test_memory_updates_appended(self, tmp_path):
        """Memory updates should be appended to MEMORY.md as a staging section."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        memory = (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")
        assert "existing fact" in memory  # Original content preserved
        assert "Dream Cycle Notes" in memory
        assert "learned something" in memory

    @pytest.mark.asyncio
    async def test_user_updates_appended(self, tmp_path):
        """User updates should be appended to USER.md as a staging section."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        user = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert "name: Dan" in user  # Original content preserved
        assert "Dream Cycle Notes" in user
        assert "user likes cats" in user

    @pytest.mark.asyncio
    async def test_heartbeat_updates_appended(self, tmp_path):
        """Heartbeat updates should be appended to HEARTBEAT.md."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        hb = (tmp_path / "HEARTBEAT.md").read_text(encoding="utf-8")
        assert "remind user at 3pm" in hb
        assert "Extracted from daily log" in hb

    @pytest.mark.asyncio
    async def test_no_updates_when_lists_empty(self, tmp_path):
        """Empty update lists should not modify any files beyond the daily log."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)

        llm_data = {
            "cleaned_daily_log": "clean",
            "memory_updates": [],
            "user_updates": [],
            "heartbeat_updates": [],
        }
        gateway = _make_gateway(llm_json=llm_data)
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        # MEMORY.md should be untouched
        memory = (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")
        assert "Dream Cycle Notes" not in memory

        # USER.md should be untouched
        user = (tmp_path / "USER.md").read_text(encoding="utf-8")
        assert "Dream Cycle Notes" not in user

        # HEARTBEAT.md should be untouched
        hb = (tmp_path / "HEARTBEAT.md").read_text(encoding="utf-8")
        assert "Extracted from" not in hb

    @pytest.mark.asyncio
    async def test_missing_memory_file_handled(self, tmp_path):
        """Missing MEMORY.md should not crash — just log a warning."""
        from auric.memory.chronicles import perform_dream_cycle

        mem_dir = tmp_path / "memories"
        mem_dir.mkdir()
        today = datetime.now().strftime("%Y-%m-%d")
        (mem_dir / f"{today}.md").write_text("# Today\nSome events.", encoding="utf-8")
        # Deliberately don't create MEMORY.md, USER.md, HEARTBEAT.md
        (tmp_path / "USER.md").write_text("# User", encoding="utf-8")
        (tmp_path / "HEARTBEAT.md").write_text("# HB", encoding="utf-8")

        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway()
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            # Should not raise
            await perform_dream_cycle(audit, gateway, config)

    @pytest.mark.asyncio
    async def test_html_entities_unescaped(self, tmp_path):
        """HTML entities in LLM output should be unescaped before writing."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)

        llm_data = {
            "cleaned_daily_log": "clean &amp; tidy",
            "memory_updates": ["Tom &amp; Jerry"],
            "user_updates": [],
            "heartbeat_updates": [],
        }
        gateway = _make_gateway(llm_json=llm_data)
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        today = datetime.now().strftime("%Y-%m-%d")
        daily = (tmp_path / "memories" / f"{today}.md").read_text(encoding="utf-8")
        assert "clean & tidy" in daily
        assert "&amp;" not in daily

        memory = (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")
        assert "Tom & Jerry" in memory

    @pytest.mark.asyncio
    async def test_empty_cleaned_log_does_not_overwrite(self, tmp_path):
        """Empty cleaned_daily_log should NOT overwrite the daily log file."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        today = datetime.now().strftime("%Y-%m-%d")
        original = (tmp_path / "memories" / f"{today}.md").read_text(encoding="utf-8")

        audit = _make_audit_logger(last_sid=None)
        llm_data = {
            "cleaned_daily_log": "",
            "memory_updates": [],
            "user_updates": [],
            "heartbeat_updates": [],
        }
        gateway = _make_gateway(llm_json=llm_data)
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        # Original daily log should be untouched
        daily = (tmp_path / "memories" / f"{today}.md").read_text(encoding="utf-8")
        assert daily == original

    @pytest.mark.asyncio
    async def test_llm_error_caught(self, tmp_path):
        """LLM errors should be caught and logged, not raised."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)
        gateway = AsyncMock()
        gateway.chat_completion = AsyncMock(side_effect=RuntimeError("LLM down"))
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            # Should not raise
            await perform_dream_cycle(audit, gateway, config)


# ---------------------------------------------------------------------------
# Tests: perform_dream_cycle — Step 4 (Dream Stories)
# ---------------------------------------------------------------------------

class TestDreamCycleStep4:

    def _setup_auric_tree(self, tmp_path: Path):
        """Create the minimal .auric file tree."""
        mem_dir = tmp_path / "memories"
        mem_dir.mkdir(exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        (mem_dir / f"{today}.md").write_text("# Today\nThings happened.", encoding="utf-8")
        (mem_dir / "MEMORY.md").write_text("## Facts\n", encoding="utf-8")
        (tmp_path / "USER.md").write_text("# User\n", encoding="utf-8")
        (tmp_path / "HEARTBEAT.md").write_text("# HB\n", encoding="utf-8")

        return today

    @pytest.mark.asyncio
    async def test_dream_story_written(self, tmp_path):
        """When enabled, a dream story should be written to DREAMS.md."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway(dream_text="I dreamt of infinite loops.")
        config = _make_config(enable_dream_stories=True)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        dreams_path = tmp_path / "memories" / "DREAMS.md"
        assert dreams_path.exists()
        content = dreams_path.read_text(encoding="utf-8")
        assert "Dream Journal" in content
        assert "I dreamt of infinite loops." in content
        assert "---" in content

    @pytest.mark.asyncio
    async def test_dream_story_creates_file(self, tmp_path):
        """DREAMS.md should be created with header if it doesn't exist."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        dreams_path = tmp_path / "memories" / "DREAMS.md"
        assert not dreams_path.exists()

        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway(dream_text="A dream!")
        config = _make_config(enable_dream_stories=True)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        assert dreams_path.exists()
        content = dreams_path.read_text(encoding="utf-8")
        assert content.startswith("# 💤 Dream Journal")

    @pytest.mark.asyncio
    async def test_dream_story_appends(self, tmp_path):
        """Multiple dream cycles should append entries, not overwrite."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        dreams_path = tmp_path / "memories" / "DREAMS.md"
        dreams_path.write_text("# 💤 Dream Journal\n\n## 2026-02-18\nOld dream.\n\n---\n\n", encoding="utf-8")

        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway(dream_text="New dream!")
        config = _make_config(enable_dream_stories=True)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        content = dreams_path.read_text(encoding="utf-8")
        assert "Old dream." in content
        assert "New dream!" in content

    @pytest.mark.asyncio
    async def test_dream_story_disabled(self, tmp_path):
        """When disabled, no dream story LLM call should be made."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)

        llm_data = {
            "cleaned_daily_log": "clean",
            "memory_updates": [],
            "user_updates": [],
            "heartbeat_updates": [],
        }
        gateway = _make_gateway(llm_json=llm_data)
        config = _make_config(enable_dream_stories=False)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        # Only 1 call (smart_model for log processing), not 2
        assert gateway.chat_completion.call_count == 1

    @pytest.mark.asyncio
    async def test_dream_story_empty_response(self, tmp_path):
        """Empty dream story response should not create/modify DREAMS.md."""
        from auric.memory.chronicles import perform_dream_cycle

        self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)
        gateway = _make_gateway(dream_text="   ")  # Whitespace only → strip() = ""
        config = _make_config(enable_dream_stories=True)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            await perform_dream_cycle(audit, gateway, config)

        dreams_path = tmp_path / "memories" / "DREAMS.md"
        assert not dreams_path.exists()

    @pytest.mark.asyncio
    async def test_dream_story_error_is_non_fatal(self, tmp_path):
        """Dream story LLM error should not crash the entire dream cycle."""
        from auric.memory.chronicles import perform_dream_cycle

        today = self._setup_auric_tree(tmp_path)
        audit = _make_audit_logger(last_sid=None)

        call_count = 0

        async def _chat_side_effect(*, messages, tier, **kwargs):
            nonlocal call_count
            call_count += 1
            if tier == "fast_model":
                raise RuntimeError("dream LLM exploded")
            return _make_llm_response(json.dumps({
                "cleaned_daily_log": "clean",
                "memory_updates": [],
                "user_updates": [],
                "heartbeat_updates": [],
            }))

        gateway = AsyncMock()
        gateway.chat_completion = AsyncMock(side_effect=_chat_side_effect)
        config = _make_config(enable_dream_stories=True)

        with patch("auric.memory.chronicles.AURIC_ROOT", tmp_path):
            # Should not raise — dream error is caught separately
            await perform_dream_cycle(audit, gateway, config)

        # Both calls were made (smart_model succeeded, fast_model raised)
        assert call_count == 2

        # Daily log should still be cleaned despite dream error
        daily = (tmp_path / "memories" / f"{today}.md").read_text(encoding="utf-8")
        assert "Dream Cycle Complete" in daily
