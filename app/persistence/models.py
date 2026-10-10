"""Application metadata; LangGraph native tables are intentionally not managed here."""

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("internal_status IN ('queued','running','closing','succeeded','failed')"),
        CheckConstraint("version >= 0"),
        CheckConstraint(
            "accepted_at < action_cutoff_at AND action_cutoff_at < close_target_at AND close_target_at < deadline_at"
        ),
        CheckConstraint("(internal_status IN ('succeeded','failed')) = (completed_at IS NOT NULL)"),
        CheckConstraint(
            "internal_status NOT IN ('succeeded','failed') OR terminal_reason IS NOT NULL"
        ),
        Index("tasks_team_time", "team_id", text("accepted_at DESC")),
        Index(
            "tasks_live_deadline",
            "deadline_at",
            postgresql_where=text("internal_status NOT IN ('succeeded','failed')"),
        ),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64))
    instruction_private: Mapped[str] = mapped_column(Text)
    destination_key: Mapped[str] = mapped_column(CHAR(64))
    internal_status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued"
    )
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sanitized_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    terminal_reason: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class TaskCredential(Base):
    __tablename__ = "task_credentials"
    __table_args__: ClassVar = {"schema": "private"}
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("app.tasks.id", ondelete="CASCADE"), primary_key=True
    )
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        CheckConstraint("attempt_no > 0"),
        CheckConstraint("next_event_sequence >= 0"),
        CheckConstraint("status IN ('running','closing','closed','uncertain')"),
        CheckConstraint("status <> 'closed' OR stopped_at IS NOT NULL"),
        UniqueConstraint("task_id", "attempt_no"),
        Index(
            "runs_single_active_task",
            "task_id",
            unique=True,
            postgresql_where=text("status IN ('running','closing','uncertain')"),
        ),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.tasks.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    thread_id: Mapped[str] = mapped_column(String(128), unique=True)
    worker_id: Mapped[str] = mapped_column(String(128))
    graph_version: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_event_sequence: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class DesktopSession(Base):
    __tablename__ = "desktop_sessions"
    __table_args__ = (
        UniqueConstraint("id", "run_id"),
        UniqueConstraint("destination_key", "epoch"),
        CheckConstraint("epoch > 0"),
        CheckConstraint("status IN ('reserved','connected','revoking','closed','uncertain')"),
        CheckConstraint(
            "status <> 'closed' OR (closed_at IS NOT NULL AND close_evidence IS NOT NULL)"
        ),
        Index(
            "sessions_single_owner",
            "destination_key",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
        ),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.task_runs.id"), unique=True)
    destination_key: Mapped[str] = mapped_column(CHAR(64))
    epoch: Mapped[int] = mapped_column(BigInteger)
    owner_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_evidence: Mapped[dict | None] = mapped_column(JSONB)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("id", "run_id"),
        UniqueConstraint("run_id", "sequence"),
        ForeignKeyConstraint(
            ["parent_event_id", "run_id"], ["app.agent_events.id", "app.agent_events.run_id"]
        ),
        CheckConstraint("sequence > 0"),
        CheckConstraint("duration_ms >= 0"),
        Index("events_run_agent_time", "run_id", "agent_name", "occurred_at"),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.task_runs.id"))
    parent_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    sequence: Mapped[int] = mapped_column(BigInteger)
    agent_name: Mapped[str] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64))
    sanitized_payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("id", "run_id"),
        ForeignKeyConstraint(
            ["event_id", "run_id"], ["app.agent_events.id", "app.agent_events.run_id"]
        ),
        CheckConstraint("byte_size >= 0"),
        CheckConstraint("expires_at > created_at"),
        Index("artifacts_expiry", "expires_at", postgresql_where=text("deleted_at IS NULL")),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.task_runs.id"))
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    private_storage_key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    mime_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ObservationRow(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("id", "run_id"),
        ForeignKeyConstraint(
            ["session_id", "run_id"], ["app.desktop_sessions.id", "app.desktop_sessions.run_id"]
        ),
        ForeignKeyConstraint(
            ["artifact_id", "run_id"], ["app.artifacts.id", "app.artifacts.run_id"]
        ),
        CheckConstraint("epoch > 0"),
        CheckConstraint("frame_revision >= 0"),
        CheckConstraint("width > 0 AND height > 0"),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.task_runs.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID)
    epoch: Mapped[int] = mapped_column(BigInteger)
    frame_revision: Mapped[int] = mapped_column(BigInteger)
    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)


class ActionAttempt(Base):
    __tablename__ = "action_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "run_id"], ["app.desktop_sessions.id", "app.desktop_sessions.run_id"]
        ),
        ForeignKeyConstraint(
            ["observation_id", "run_id"], ["app.observations.id", "app.observations.run_id"]
        ),
        ForeignKeyConstraint(
            ["outcome_observation_id", "run_id"], ["app.observations.id", "app.observations.run_id"]
        ),
        CheckConstraint("epoch > 0"),
        CheckConstraint("risk IN ('navigation','edit','commit','export')"),
        CheckConstraint("status IN ('proposed','rejected','intent','sent','verified','uncertain')"),
        CheckConstraint("expires_at > proposed_at"),
        CheckConstraint(
            "status <> 'verified' OR (verified_at IS NOT NULL AND outcome_observation_id IS NOT NULL)"
        ),
        Index("actions_run_time", "run_id", "proposed_at"),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.task_runs.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID)
    observation_id: Mapped[uuid.UUID] = mapped_column(UUID)
    epoch: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))
    risk: Mapped[str] = mapped_column(String(16))
    sanitized_arguments: Mapped[dict] = mapped_column(JSONB)
    expected_effect: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_observation_id: Mapped[uuid.UUID | None] = mapped_column(UUID)


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("task_id", "event_type"),
        CheckConstraint("attempts >= 0"),
        Index(
            "outbox_pending",
            "available_at",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.tasks.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"
    __table_args__: ClassVar = {"schema": "app"}
    component: Mapped[str] = mapped_column(String(32), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready: Mapped[bool] = mapped_column(Boolean)
    sanitized_details: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
