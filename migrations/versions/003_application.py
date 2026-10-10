"""Application schema 003; immutable DDL, no native graph tables."""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE app.agent_events (\n    id uuid PRIMARY KEY,\n    run_id uuid NOT NULL REFERENCES app.task_runs(id),\n    parent_event_id uuid,\n    sequence bigint NOT NULL CHECK (sequence > 0),\n    agent_name varchar(64) NOT NULL,\n    node_name varchar(64),\n    event_type varchar(64) NOT NULL,\n    sanitized_payload jsonb NOT NULL DEFAULT '{}',\n    duration_ms integer CHECK (duration_ms >= 0),\n    occurred_at timestamptz NOT NULL,\n    UNIQUE(id,run_id),\n    UNIQUE(run_id,sequence),\n    FOREIGN KEY(parent_event_id,run_id) REFERENCES app.agent_events(id,run_id)\n)"
    )
    op.execute(
        "CREATE INDEX events_run_agent_time ON app.agent_events(run_id,agent_name,occurred_at)"
    )
    op.execute(
        "CREATE TABLE app.artifacts (\n    id uuid PRIMARY KEY,\n    run_id uuid NOT NULL REFERENCES app.task_runs(id),\n    event_id uuid,\n    private_storage_key text NOT NULL UNIQUE,\n    sha256 char(64) NOT NULL,\n    mime_type varchar(128) NOT NULL,\n    byte_size bigint NOT NULL CHECK (byte_size >= 0),\n    created_at timestamptz NOT NULL,\n    expires_at timestamptz NOT NULL,\n    deleted_at timestamptz,\n    UNIQUE(id,run_id),\n    FOREIGN KEY(event_id,run_id) REFERENCES app.agent_events(id,run_id),\n    CHECK (expires_at > created_at)\n)"
    )
    op.execute(
        "CREATE INDEX artifacts_expiry ON app.artifacts(expires_at) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE TABLE app.observations (\n    id uuid PRIMARY KEY,\n    run_id uuid NOT NULL REFERENCES app.task_runs(id),\n    session_id uuid NOT NULL,\n    epoch bigint NOT NULL CHECK (epoch > 0),\n    frame_revision bigint NOT NULL CHECK (frame_revision >= 0),\n    artifact_id uuid NOT NULL,\n    captured_at timestamptz NOT NULL,\n    width integer NOT NULL CHECK (width > 0),\n    height integer NOT NULL CHECK (height > 0),\n    UNIQUE(id,run_id),\n    FOREIGN KEY(session_id,run_id) REFERENCES app.desktop_sessions(id,run_id),\n    FOREIGN KEY(artifact_id,run_id) REFERENCES app.artifacts(id,run_id)\n)"
    )
    op.execute(
        "CREATE TABLE app.action_attempts (\n    id uuid PRIMARY KEY, -- id de propuesta: redelivery no crea otra acción\n    run_id uuid NOT NULL REFERENCES app.task_runs(id),\n    session_id uuid NOT NULL,\n    observation_id uuid NOT NULL,\n    epoch bigint NOT NULL CHECK (epoch > 0),\n    kind varchar(32) NOT NULL,\n    risk varchar(16) NOT NULL CHECK (risk IN ('navigation','edit','commit','export')),\n    sanitized_arguments jsonb NOT NULL,\n    expected_effect text NOT NULL,\n    status varchar(16) NOT NULL CHECK (status IN ('proposed','rejected','intent','sent','verified','uncertain')),\n    proposed_at timestamptz NOT NULL,\n    expires_at timestamptz NOT NULL,\n    sent_at timestamptz,\n    verified_at timestamptz,\n    outcome_observation_id uuid,\n    FOREIGN KEY(session_id,run_id) REFERENCES app.desktop_sessions(id,run_id),\n    FOREIGN KEY(observation_id,run_id) REFERENCES app.observations(id,run_id),\n    FOREIGN KEY(outcome_observation_id,run_id) REFERENCES app.observations(id,run_id),\n    CHECK (expires_at > proposed_at),\n    CHECK (status <> 'verified' OR (verified_at IS NOT NULL AND outcome_observation_id IS NOT NULL))\n)"
    )
    op.execute("CREATE INDEX actions_run_time ON app.action_attempts(run_id,proposed_at)")


def downgrade():
    op.drop_table("action_attempts", schema="app")
    op.drop_table("observations", schema="app")
    op.drop_table("artifacts", schema="app")
    op.drop_table("agent_events", schema="app")
