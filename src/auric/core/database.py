import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from uuid import uuid4

from pydantic import Json
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
        self.engine = create_async_engine(connection_string, echo=False)

    async def init_db(self):
        """Creates tables if they don't exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

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
