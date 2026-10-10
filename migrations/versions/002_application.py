"""Application schema 002; immutable DDL, no native graph tables."""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE app.task_runs (\n    id uuid PRIMARY KEY,\n    task_id uuid NOT NULL REFERENCES app.tasks(id),\n    attempt_no integer NOT NULL CHECK (attempt_no > 0),\n    thread_id varchar(128) NOT NULL UNIQUE,\n    worker_id varchar(128) NOT NULL,\n    graph_version varchar(64) NOT NULL,\n    model_version varchar(128) NOT NULL,\n    status varchar(16) NOT NULL CHECK (status IN ('running','closing','closed','uncertain')),\n    started_at timestamptz NOT NULL,\n    stopped_at timestamptz,\n    heartbeat_at timestamptz NOT NULL,\n    next_event_sequence bigint NOT NULL DEFAULT 0 CHECK (next_event_sequence >= 0),\n    UNIQUE(task_id, attempt_no),\n    CHECK (status <> 'closed' OR stopped_at IS NOT NULL)\n)"
    )
    op.execute(
        "CREATE UNIQUE INDEX runs_single_active_task ON app.task_runs(task_id)\n    WHERE status IN ('running','closing','uncertain')"
    )
    op.execute(
        "CREATE TABLE app.desktop_sessions (\n    id uuid PRIMARY KEY,\n    run_id uuid NOT NULL UNIQUE REFERENCES app.task_runs(id),\n    destination_key char(64) NOT NULL,\n    epoch bigint NOT NULL CHECK (epoch > 0),\n    owner_id varchar(128) NOT NULL,\n    status varchar(16) NOT NULL CHECK (status IN ('reserved','connected','revoking','closed','uncertain')),\n    lease_expires_at timestamptz NOT NULL,\n    opened_at timestamptz,\n    revoked_at timestamptz,\n    closed_at timestamptz,\n    close_evidence jsonb,\n    UNIQUE(id, run_id),\n    UNIQUE(destination_key, epoch),\n    CHECK (status <> 'closed' OR (closed_at IS NOT NULL AND close_evidence IS NOT NULL))\n)"
    )
    op.execute(
        "CREATE UNIQUE INDEX sessions_single_owner ON app.desktop_sessions(destination_key)\n    WHERE status <> 'closed'"
    )
    op.execute(
        "CREATE TABLE app.service_heartbeats (\n    component varchar(32) NOT NULL,\n    instance_id varchar(128) NOT NULL,\n    heartbeat_at timestamptz NOT NULL,\n    ready boolean NOT NULL,\n    sanitized_details jsonb NOT NULL DEFAULT '{}',\n    PRIMARY KEY(component,instance_id)\n)"
    )


def downgrade():
    op.drop_table("service_heartbeats", schema="app")
    op.drop_table("desktop_sessions", schema="app")
    op.drop_table("task_runs", schema="app")
