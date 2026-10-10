"""Application schema 001; immutable DDL, no native graph tables."""

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE SCHEMA IF NOT EXISTS private")
    op.execute(
        "CREATE TABLE app.tasks (\n    id uuid PRIMARY KEY,\n    team_id varchar(64) NOT NULL,\n    instruction_private text NOT NULL,\n    destination_key char(64) NOT NULL,\n    internal_status varchar(16) NOT NULL DEFAULT 'queued'\n        CHECK (internal_status IN ('queued','running','closing','succeeded','failed')),\n    accepted_at timestamptz NOT NULL,\n    action_cutoff_at timestamptz NOT NULL,\n    close_target_at timestamptz NOT NULL,\n    deadline_at timestamptz NOT NULL,\n    completed_at timestamptz,\n    sanitized_summary text NOT NULL DEFAULT '',\n    terminal_reason varchar(64),\n    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),\n    CHECK (accepted_at < action_cutoff_at AND action_cutoff_at < close_target_at\n        AND close_target_at < deadline_at),\n    CHECK ((internal_status IN ('succeeded','failed')) = (completed_at IS NOT NULL)),\n    CHECK (internal_status NOT IN ('succeeded','failed') OR terminal_reason IS NOT NULL)\n)"
    )
    op.execute("CREATE INDEX tasks_team_time ON app.tasks(team_id, accepted_at DESC)")
    op.execute(
        "CREATE INDEX tasks_live_deadline ON app.tasks(deadline_at)\n    WHERE internal_status NOT IN ('succeeded','failed')"
    )
    op.execute(
        "CREATE TABLE private.task_credentials (\n    task_id uuid PRIMARY KEY REFERENCES app.tasks(id) ON DELETE CASCADE,\n    encrypted_payload bytea NOT NULL,\n    key_version varchar(32) NOT NULL,\n    expires_at timestamptz NOT NULL\n)"
    )
    op.execute(
        "CREATE TABLE app.outbox (\n    id uuid PRIMARY KEY,\n    task_id uuid NOT NULL REFERENCES app.tasks(id),\n    event_type varchar(64) NOT NULL,\n    created_at timestamptz NOT NULL,\n    available_at timestamptz NOT NULL,\n    claimed_by varchar(128),\n    claimed_until timestamptz,\n    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),\n    published_at timestamptz,\n    UNIQUE(task_id,event_type)\n)"
    )
    op.execute(
        "CREATE INDEX outbox_pending ON app.outbox(available_at,created_at) WHERE published_at IS NULL"
    )


def downgrade():
    op.drop_table("outbox", schema="app")
    op.drop_table("task_credentials", schema="private")
    op.drop_table("tasks", schema="app")
