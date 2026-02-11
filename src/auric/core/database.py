import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any, Dict
from uuid import uuid4

from pydantic import Json
from sqlalchemy import JSON, Column, text, delete, func, desc
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import Field, SQLModel, Session, select
from sqlmodel.ext.asyncio.session import AsyncSession


class TaskExecution(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    goal: str
    status: str = Field(default="RUNNING")
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error_log: Optional[str] = None


class TaskStep(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="taskexecution.id")
    tool_name: Optional[str] = None
    tool_input: Optional[str] = None
    tool_output: Optional[str] = None
    thought_process: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ChatMessage(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    role: str  # USER, AGENT, THOUGHT
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    session_id: Optional[str] = None


class Session(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class LLMInteraction(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now)
    model: str
    session_id: Optional[str] = None
    input_messages: List[Any] = Field(sa_column=Column(JSON))
    output_content: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_cost: Optional[float] = None
    duration_ms: float


class AuditLogger:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            # Default to ~/.auric/auric.db
            db_path = Path.home() / ".auric" / "auric.db"
        
        self.db_path = db_path
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create async engine
        connection_string = f"sqlite+aiosqlite:///{self.db_path}"
        # increase timeout to prevent "database is locked"
        self.engine = create_async_engine(connection_string, echo=False, connect_args={"timeout": 60})

    async def init_db(self):
        """Creates tables if they don't exist and handles migrations."""
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            
            # --- Migrations ---
            # Check for missing 'session_id' in LLMInteraction
            try:
                # Get table info
                columns_result = await conn.execute(text("PRAGMA table_info(llminteraction);"))
                columns = [row.name for row in columns_result.fetchall()]
                
                if "session_id" not in columns:
                    # Add column
                    await conn.execute(text("ALTER TABLE llminteraction ADD COLUMN session_id VARCHAR;"))
            except Exception as e:
                # Ignore if table doesn't exist or other error (create_all should have handled table creation)
                pass

            # Check for missing 'session_id' in ChatMessage
            try:
                columns_result = await conn.execute(text("PRAGMA table_info(chatmessage);"))
                columns = [row.name for row in columns_result.fetchall()]
                
                if "session_id" not in columns:
                    await conn.execute(text("ALTER TABLE chatmessage ADD COLUMN session_id VARCHAR;"))
            except Exception as e:
                pass
            # ------------------

            # Enable WAL mode for better concurrency
            # Retrying a few times in case of transient locks
            for i in range(5):
                try:
                    await conn.execute(text("PRAGMA journal_mode=WAL;"))
                    await conn.execute(text("PRAGMA synchronous=NORMAL;"))
                    break
                except Exception as e:
                    if "database is locked" in str(e) and i < 4:
                        await asyncio.sleep(1)
                    else:
                        raise e

    async def create_task(self, goal: str) -> str:
        """Starts a new task execution log and returns the task ID."""
        task = TaskExecution(goal=goal)
        async with AsyncSession(self.engine) as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task.id

    async def log_step(self, task_id: str, step_data: TaskStep) -> None:
        """Appends a step to a task."""
        # Ensure the step is linked to the correct task
        step_data.task_id = task_id
        async with AsyncSession(self.engine) as session:
            session.add(step_data)
            await session.commit()

    async def update_status(self, task_id: str, status: str, error_log: Optional[str] = None) -> None:
        """Updates the status of a task.
        
        If status is COMPLETED or FAILED, sets completed_at.
        If status is PENDING_APPROVAL, completed_at remains None.
        """
        async with AsyncSession(self.engine) as session:
            statement = select(TaskExecution).where(TaskExecution.id == task_id)
            result = await session.exec(statement)
            task = result.one_or_none()
            
            if task:
                task.status = status
                if error_log:
                    task.error_log = error_log
                
                # Update completed_at only for terminal states
                if status in ["COMPLETED", "FAILED"]:
                     if task.completed_at is None: # Only set if not already set
                        task.completed_at = datetime.now()
                
                # Check for PENDING_APPROVAL specific logic (ensure NOT completed)
                if status == "PENDING_APPROVAL":
                    task.completed_at = None

                session.add(task)
                await session.commit()

    async def get_pending_approval_task(self) -> Optional[TaskExecution]:
        """
        Retrieves the latest task that is in PENDING_APPROVAL status.
        """
        async with AsyncSession(self.engine) as session:
            # Order by started_at desc to get the most recent one
            statement = select(TaskExecution).where(TaskExecution.status == "PENDING_APPROVAL").order_by(TaskExecution.started_at.desc()).limit(1)
            result = await session.exec(statement)
            return result.one_or_none()

    async def log_chat(self, role: str, content: str, session_id: Optional[str] = None) -> None:
        """Logs a chat message."""
        message = ChatMessage(role=role, content=content, session_id=session_id)
        async with AsyncSession(self.engine) as session:
            session.add(message)
            await session.commit()

    async def get_chat_history(self, limit: int = 50, session_id: Optional[str] = None) -> List[ChatMessage]:
        """Retrieves recent chat history."""
        async with AsyncSession(self.engine) as session:
            statement = select(ChatMessage)
            
            if session_id:
               statement = statement.where(ChatMessage.session_id == session_id)
            
            statement = statement.order_by(ChatMessage.timestamp.desc()).limit(limit)
            result = await session.exec(statement)
            # Reverse to get chronological order for display
            return list(reversed(result.all()))

    async def get_sessions(self) -> List[Dict[str, Any]]:
        """Retrieves a list of chat sessions."""
        async with AsyncSession(self.engine) as session:
            # 1. Get all sessions with message counts and last active time
            statement = select(
                ChatMessage.session_id,
                func.max(ChatMessage.timestamp).label("last_active"),
                func.count(ChatMessage.id).label("fn_count")
            ).group_by(ChatMessage.session_id).order_by(desc("last_active"))
            
            result = await session.exec(statement)
            sessions_data = []
            
            for row in result.all():
                sess_id, last_active, count = row
                if not sess_id: continue

                # 2. Get Session Name (if exists)
                # We could join, but let's just query for now or rely on separate query if needed.
                # Actually, let's just get the name from Session table.
                
                name = "Unknown Session"
                
                # Fetch Session object
                sess_obj = await session.get(Session, sess_id)
                if sess_obj and sess_obj.name:
                    name = sess_obj.name
                else:
                    # Generate name from first message
                    msg_stmt = select(ChatMessage).where(ChatMessage.session_id == sess_id).order_by(ChatMessage.timestamp.asc()).limit(1)
                    first_msg = (await session.exec(msg_stmt)).one_or_none()
                    if first_msg:
                        # Truncate to 30 chars
                        name = first_msg.content[:30] + "..." if len(first_msg.content) > 30 else first_msg.content
                    else:
                        name = f"Session {sess_id[:8]}"

                sessions_data.append({
                    "session_id": sess_id,
                    "name": name,
                    "last_active": last_active.isoformat() if last_active else None,
                    "message_count": count
                })
            
            return sessions_data

    async def log_llm(self, interaction: LLMInteraction) -> None:
        """Logs an LLM interaction."""
        async with AsyncSession(self.engine) as session:
            session.add(interaction)
            await session.commit()

    async def get_llm_logs(self, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        """Retrieves paginated LLM interaction logs."""
        async with AsyncSession(self.engine) as session:
            # Get total count
            count_statement = select(func.count(LLMInteraction.id))
            count_result = await session.exec(count_statement)
            total = count_result.one()

            # Get items
            statement = select(LLMInteraction).order_by(LLMInteraction.timestamp.desc()).offset(offset).limit(limit)
            result = await session.exec(statement)
            items = result.all()

            return {
                "total": total,
                "items": items
            }
            
    async def clear_chat_history(self) -> None:
        """Clears all chat history."""
        async with AsyncSession(self.engine) as session:
            await session.exec(delete(ChatMessage))
            await session.commit()

    async def create_session(self, name: Optional[str] = None) -> str:
        """Creates a new session and returns its ID."""
        session_id = str(uuid4())
        session = Session(id=session_id, name=name)
        async with AsyncSession(self.engine) as db:
            db.add(session)
            await db.commit()
        return session_id

    async def rename_session(self, session_id: str, new_name: str) -> None:
        """Renames a session."""
        async with AsyncSession(self.engine) as db:
            statement = select(Session).where(Session.id == session_id)
            results = await db.exec(statement)
            session = results.one_or_none()
            if session:
                session.name = new_name
                db.add(session)
                await db.commit()
