import asyncio
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiofiles
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from auric.core.database import (
    AuditLogger, 
    TaskExecution, 
    TaskStep, 
    ChatMessage, 
    Session, 
    LLMInteraction, 
    Heartbeat
)

@pytest.fixture
async def temp_db_path(tmp_path):
    return tmp_path / "test_auric.db"

@pytest.fixture
async def audit_logger(temp_db_path):
    logger = AuditLogger(db_path=temp_db_path)
    await logger.init_db()
    yield logger
    await logger.close()

@pytest.mark.asyncio
async def test_audit_logger_default_path(tmp_path):
    # Mock AURIC_ROOT to a temp directory
    with patch("auric.core.database.AURIC_ROOT", tmp_path):
        logger = AuditLogger()
        assert logger.db_path == tmp_path / "auric.db"
        assert logger.db_path.parent.exists()

@pytest.mark.asyncio
async def test_init_db_creates_tables(temp_db_path):
    async with AuditLogger(db_path=temp_db_path) as logger:
        await logger.init_db()
        assert temp_db_path.exists()
        
        # Run init again to test migration/idempotency
        await logger.init_db()

@pytest.mark.asyncio
async def test_migrations_add_columns(temp_db_path):
    async with AuditLogger(db_path=temp_db_path) as logger:
        await logger.init_db()
        
        async with logger.engine.connect() as conn:
            from sqlalchemy import text
            # Use result.all() and inspect rows
            res = await conn.execute(text("PRAGMA table_info(llminteraction);"))
            rows = res.all()
            columns = [row[1] for row in rows] # row[1] is name
            assert "session_id" in columns

            res = await conn.execute(text("PRAGMA table_info(chatmessage);"))
            rows = res.all()
            columns = [row[1] for row in rows]
            assert "session_id" in columns

@pytest.mark.asyncio
async def test_task_operations(audit_logger):
    # Create task
    task_id = await audit_logger.create_task("Test Goal")
    assert task_id is not None
    
    # Log step
    step = TaskStep(thought_process="Thinking...")
    await audit_logger.log_step(task_id, step)
    
    # Update status
    await audit_logger.update_status(task_id, "RUNNING")
    
    async with AsyncSession(audit_logger.engine) as session:
        task = await session.get(TaskExecution, task_id)
        assert task.goal == "Test Goal"
        assert task.status == "RUNNING"
        assert task.completed_at is None
        
        # Verify step
        statement = select(TaskStep).where(TaskStep.task_id == task_id)
        result = await session.exec(statement)
        steps = result.all()
        assert len(steps) == 1
        assert steps[0].thought_process == "Thinking..."

    # Complete task
    await audit_logger.update_status(task_id, "COMPLETED")
    async with AsyncSession(audit_logger.engine) as session:
        task = await session.get(TaskExecution, task_id)
        assert task.status == "COMPLETED"
        assert task.completed_at is not None

    # Failed task with error log
    await audit_logger.update_status(task_id, "FAILED", error_log="Something went wrong")
    async with AsyncSession(audit_logger.engine) as session:
        task = await session.get(TaskExecution, task_id)
        assert task.status == "FAILED"
        assert task.error_log == "Something went wrong"

@pytest.mark.asyncio
async def test_pending_approval_task(audit_logger):
    task_id = await audit_logger.create_task("Need approval")
    await audit_logger.update_status(task_id, "PENDING_APPROVAL")
    
    task = await audit_logger.get_pending_approval_task()
    assert task is not None
    assert task.id == task_id
    assert task.status == "PENDING_APPROVAL"
    assert task.completed_at is None

@pytest.mark.asyncio
async def test_chat_operations(audit_logger):
    # Log some chats
    await audit_logger.log_chat("USER", "Hello", session_id="sess1")
    await audit_logger.log_chat("AGENT", "Hi there", session_id="sess1")
    await audit_logger.log_chat("USER", "Other session", session_id="sess2")
    
    # Get history for sess1
    history = await audit_logger.get_chat_history(session_id="sess1")
    assert len(history) == 2
    assert history[0].content == "Hello"
    assert history[1].content == "Hi there"
    
    # Get all history
    history = await audit_logger.get_chat_history(limit=10)
    assert len(history) == 3
    
    # Clear history
    await audit_logger.clear_chat_history()
    history = await audit_logger.get_chat_history()
    assert len(history) == 0

@pytest.mark.asyncio
async def test_session_management(audit_logger):
    # Create session
    sess_id = await audit_logger.create_session(name="Test Session")
    assert sess_id is not None
    
    # Create session with specific ID
    custom_id = "custom-uuid"
    await audit_logger.create_session(name="Custom Session", session_id=custom_id)
    
    # Get session
    sess = await audit_logger.get_session(custom_id)
    assert sess.name == "Custom Session"
    
    # Rename session
    await audit_logger.rename_session(custom_id, "New Name")
    sess = await audit_logger.get_session(custom_id)
    assert sess.name == "New Name"
    
    # Session collision (line 306)
    existing_id = await audit_logger.create_session(name="Initial")
    collision_id = await audit_logger.create_session(name="Collision", session_id=existing_id)
    assert collision_id == existing_id
    sess = await audit_logger.get_session(existing_id)
    assert sess.name == "Initial" # Name shouldn't change
    
    # Get last active session
    await audit_logger.log_chat("USER", "ping", session_id=custom_id)
    last_id = await audit_logger.get_last_active_session_id()
    assert last_id == custom_id

@pytest.mark.asyncio
async def test_get_sessions_and_name_generation(audit_logger):
    # 1. Session with explicit name
    s1 = await audit_logger.create_session(name="Explicit Name", session_id="s1")
    await audit_logger.log_chat("USER", "Msg 1", session_id="s1")
    
    # 2. Session without name (should use first message)
    s2 = await audit_logger.create_session(session_id="s2")
    await audit_logger.log_chat("USER", "This is a very long message that should be truncated", session_id="s2")
    
    # 3. Session without name and no messages
    s3 = await audit_logger.create_session(session_id="s3")
    
    sessions = await audit_logger.get_sessions()
    
    # Find s1
    sess1 = next(s for s in sessions if s["session_id"] == "s1")
    assert sess1["name"] == "Explicit Name"
    assert sess1["message_count"] == 1
    
    # Find s2
    sess2 = next(s for s in sessions if s["session_id"] == "s2")
    assert sess2["name"].startswith("This is a very long message")
    assert sess2["name"].endswith("...")
    
    # s3 might not show up if it has no messages because get_sessions groups by ChatMessage.session_id
    # Let's check the implementation of get_sessions
    # It does select(ChatMessage.session_id ...).group_by(ChatMessage.session_id)
    # So s3 won't be in the list.

@pytest.mark.asyncio
async def test_llm_and_heartbeat_logging(audit_logger):
    # Log LLM
    interaction = LLMInteraction(
        model="gpt-4",
        input_messages=[{"role": "user", "content": "hi"}],
        output_content="hello",
        duration_ms=100.0,
        prompt_tokens=10,
        completion_tokens=5,
        total_cost=0.001
    )
    await audit_logger.log_llm(interaction)
    
    logs = await audit_logger.get_llm_logs(limit=10)
    assert logs["total"] == 1
    assert logs["items"][0].model == "gpt-4"
    
    # Log Heartbeat
    await audit_logger.log_heartbeat(status="ALIVE", meta={"load": 0.5})
    
    async with AsyncSession(audit_logger.engine) as session:
        statement = select(Heartbeat)
        result = await session.exec(statement)
        hb = result.one()
        assert hb.status == "ALIVE"
        assert '"load": 0.5' in hb.metadata_json

@pytest.mark.asyncio
async def test_summarization_content_retrieval(audit_logger):
    await audit_logger.log_chat("USER", "Message 1", session_id="sess1")
    await audit_logger.log_chat("THOUGHT", "Thinking...", session_id="sess1")
    await audit_logger.log_chat("AGENT", "Response 1", session_id="sess1")
    
    content = await audit_logger.get_recent_session_content("sess1")
    assert "User: Message 1" in content
    assert "Agent: Response 1" in content
    assert "THOUGHT" not in content

@pytest.mark.asyncio
async def test_summarize_session(audit_logger):
    # Mock gateway
    gateway = AsyncMock()
    gateway.chat_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Sample Summary"))]
    )
    
    # Setup some chat
    await audit_logger.log_chat("USER", "Do something", session_id="sess1")
    
    # Mock AURIC_ROOT for file writing
    with patch("auric.core.database.AURIC_ROOT") as mock_root:
        mock_root.__truediv__.return_value = mock_root
        mock_root.parent = mock_root # allow nested div
        
        # We need to mock the path behavior properly
        # AURIC_ROOT / "memories" / f"{today}.md"
        mock_memories_dir = MagicMock()
        mock_root.__truediv__.side_effect = lambda x: mock_memories_dir if x == "memories" else mock_root
        
        # Actually it's easier to just mock the file open
        # but AuditLogger uses aiofiles
        
        # Let's try to mock AURIC_ROOT so it points to a real temp dir
        temp_memories = audit_logger.db_path.parent / "memories"
        temp_memories.mkdir()
        
        with patch("auric.core.database.AURIC_ROOT", audit_logger.db_path.parent):
            await audit_logger.summarize_session("sess1", gateway)
            
            today = datetime.now().strftime("%Y-%m-%d")
            memory_file = temp_memories / f"{today}.md"
            assert memory_file.exists()
            
            async with audit_logger.engine.connect() as conn: # dummy wait
                 pass
            
            # Read content
            async with aiofiles.open(memory_file, mode='r', encoding='utf-8') as f:
                content = await f.read()
                assert "Sample Summary" in content

@pytest.mark.asyncio
async def test_summarize_session_null(audit_logger):
    gateway = AsyncMock()
    gateway.chat_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="NULL"))]
    )
    
    await audit_logger.log_chat("USER", "Nothing important", session_id="sess1")
    
    with patch("auric.core.database.AURIC_ROOT", audit_logger.db_path.parent):
        await audit_logger.summarize_session("sess1", gateway)
        
        today = datetime.now().strftime("%Y-%m-%d")
        memory_file = audit_logger.db_path.parent / "memories" / f"{today}.md"
        assert not memory_file.exists()

@pytest.mark.asyncio
async def test_init_db_wal_retry(temp_db_path):
    # Create logger with a mock engine to trigger retry
    async with AuditLogger(db_path=temp_db_path) as logger:
        
        mock_conn = AsyncMock()
    # Mock responses for PRAGMA table_info calls and the WAL setup
    mock_res_empty = MagicMock()
    mock_res_empty.fetchall.return_value = []
    
    # Sequence of returns for conn.execute:
    # 1. PRAGMA table_info(llminteraction)
    # 2. ALTER TABLE llminteraction ...
    # 3. PRAGMA table_info(chatmessage)
    # 4. ALTER TABLE chatmessage ...
    # 5. PRAGMA journal_mode=WAL; (will fail once)
    # 6. PRAGMA journal_mode=WAL; (succeed)
    # 7. PRAGMA synchronous=NORMAL;
    
    mock_conn.execute.side_effect = [
        mock_res_empty, # 1
        None, # 2
        mock_res_empty, # 3
        None, # 4
        Exception("database is locked"), # 5
        None, # 6
        None # 7
    ]
    
    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__.return_value = mock_conn
    logger.engine = mock_engine
    
    with patch("auric.core.database.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await logger.init_db()
        assert mock_sleep.called
        assert mock_conn.execute.call_count == 7

@pytest.mark.asyncio
async def test_summarize_session_missing_id(audit_logger):
    # Should return early
    await audit_logger.summarize_session("", None)
    
@pytest.mark.asyncio
async def test_summarize_session_no_content(audit_logger):
    gateway = AsyncMock()
    await audit_logger.summarize_session("empty_sess", gateway)
    gateway.chat_completion.assert_not_called()

@pytest.mark.asyncio
async def test_summarize_session_error(audit_logger, capsys):
    gateway = AsyncMock()
    gateway.chat_completion.side_effect = Exception("LLM Down")
    
    await audit_logger.log_chat("USER", "Message", session_id="sess1")
    
    await audit_logger.summarize_session("sess1", gateway)
    
    captured = capsys.readouterr()
    assert "Error summarizing session: LLM Down" in captured.out

@pytest.mark.asyncio
async def test_init_db_wal_permanent_failure(temp_db_path):
    async with AuditLogger(db_path=temp_db_path) as logger:
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("permanent lock")
    
    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__.return_value = mock_conn
    logger.engine = mock_engine
    
    with patch("auric.core.database.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(Exception, match="permanent lock"):
            await logger.init_db()

@pytest.mark.asyncio
async def test_init_db_migration_exception_covers(temp_db_path):
    async with AuditLogger(db_path=temp_db_path) as logger:
        mock_conn = AsyncMock()
        
        # Fail during PRAGMA table_info to trigger except block
        mock_conn.execute.side_effect = Exception("Migration fail")
    
    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__.return_value = mock_conn
    logger.engine = mock_engine
    
    # Should hit try-excepts at 92 and 105, then fail at 119 and hit 126
    with pytest.raises(Exception, match="Migration fail"):
        await logger.init_db()
    
@pytest.mark.asyncio
async def test_get_sessions_fallback_name(audit_logger):
    # We need to trigger line 238
    # get_sessions fetches from ChatMessage grouping by session_id
    # then it tries to find first_msg. If it doesn't find it (race?), it hits fallback.
    
    await audit_logger.log_chat("USER", "Msg", session_id="ghost_sess")
    
    # Mock the internal session.exec to return None for the first message query
    original_exec = AsyncSession.exec
    
    call_count = [0]
    async def mock_exec(self, statement, *args, **kwargs):
        # The first message query has "order_by(ChatMessage.timestamp.asc()).limit(1)"
        if "ORDER BY chatmessage.timestamp ASC" in str(statement):
            return MagicMock(one_or_none=lambda: None)
        return await original_exec(self, statement, *args, **kwargs)

    with patch("sqlmodel.ext.asyncio.session.AsyncSession.exec", mock_exec):
        sessions = await audit_logger.get_sessions()
        ghost = next(s for s in sessions if s["session_id"] == "ghost_sess")
        assert ghost["name"].startswith("Session ghost_se")

@pytest.mark.asyncio
async def test_get_sessions_with_none_id(audit_logger):
    await audit_logger.log_chat("USER", "No session", session_id=None)
    sessions = await audit_logger.get_sessions()
    # Should skip the one with None session_id (line 218)
    assert len(sessions) == 0
