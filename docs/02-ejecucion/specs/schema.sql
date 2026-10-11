-- SPEC de referencia. Implementar con SQLAlchemy/Alembic, no ejecutar como bootstrap final.
-- UUID generados por aplicación. Tiempos UTC; no se almacena estado público redundante.
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS private;
CREATE SCHEMA IF NOT EXISTS graph_checkpoints;
CREATE SCHEMA IF NOT EXISTS graph_store;

CREATE TABLE app.tasks (
    id uuid PRIMARY KEY,
    team_id varchar(64) NOT NULL,
    instruction_private text NOT NULL,
    destination_key char(64) NOT NULL,
    internal_status varchar(16) NOT NULL DEFAULT 'queued'
        CHECK (internal_status IN ('queued','running','closing','succeeded','failed')),
    accepted_at timestamptz NOT NULL,
    action_cutoff_at timestamptz NOT NULL,
    close_target_at timestamptz NOT NULL,
    deadline_at timestamptz NOT NULL,
    completed_at timestamptz,
    sanitized_summary text NOT NULL DEFAULT '',
    terminal_reason varchar(64),
    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
    CHECK (accepted_at < action_cutoff_at AND action_cutoff_at < close_target_at
        AND close_target_at < deadline_at),
    CHECK ((internal_status IN ('succeeded','failed')) = (completed_at IS NOT NULL)),
    CHECK (internal_status NOT IN ('succeeded','failed') OR terminal_reason IS NOT NULL)
);
CREATE INDEX tasks_team_time ON app.tasks(team_id, accepted_at DESC);
CREATE INDEX tasks_live_deadline ON app.tasks(deadline_at)
    WHERE internal_status NOT IN ('succeeded','failed');

-- Cifrado autenticado por aplicación; clave fuera de DB. No plaintext del escritorio.
CREATE TABLE private.task_credentials (
    task_id uuid PRIMARY KEY REFERENCES app.tasks(id) ON DELETE CASCADE,
    encrypted_payload bytea NOT NULL,
    key_version varchar(32) NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE app.task_runs (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES app.tasks(id),
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    thread_id varchar(128) NOT NULL UNIQUE,
    worker_id varchar(128) NOT NULL,
    graph_version varchar(64) NOT NULL,
    model_version varchar(128) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('running','closing','closed','uncertain')),
    started_at timestamptz NOT NULL,
    stopped_at timestamptz,
    heartbeat_at timestamptz NOT NULL,
    next_event_sequence bigint NOT NULL DEFAULT 0 CHECK (next_event_sequence >= 0),
    UNIQUE(task_id, attempt_no),
    CHECK (status <> 'closed' OR stopped_at IS NOT NULL)
);
CREATE UNIQUE INDEX runs_single_active_task ON app.task_runs(task_id)
    WHERE status IN ('running','closing','uncertain');

CREATE TABLE app.desktop_sessions (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL UNIQUE REFERENCES app.task_runs(id),
    destination_key char(64) NOT NULL,
    epoch bigint NOT NULL CHECK (epoch > 0),
    owner_id varchar(128) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('reserved','connected','revoking','closed','uncertain')),
    lease_expires_at timestamptz NOT NULL,
    opened_at timestamptz,
    revoked_at timestamptz,
    closed_at timestamptz,
    close_evidence jsonb,
    UNIQUE(id, run_id),
    UNIQUE(destination_key, epoch),
    CHECK (status <> 'closed' OR (closed_at IS NOT NULL AND close_evidence IS NOT NULL))
);
CREATE UNIQUE INDEX sessions_single_owner ON app.desktop_sessions(destination_key)
    WHERE status <> 'closed';

CREATE TABLE app.agent_events (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES app.task_runs(id),
    parent_event_id uuid,
    sequence bigint NOT NULL CHECK (sequence > 0),
    agent_name varchar(64) NOT NULL,
    node_name varchar(64),
    event_type varchar(64) NOT NULL,
    sanitized_payload jsonb NOT NULL DEFAULT '{}',
    duration_ms integer CHECK (duration_ms >= 0),
    occurred_at timestamptz NOT NULL,
    UNIQUE(id,run_id),
    UNIQUE(run_id,sequence),
    FOREIGN KEY(parent_event_id,run_id) REFERENCES app.agent_events(id,run_id)
);
CREATE INDEX events_run_agent_time ON app.agent_events(run_id,agent_name,occurred_at);

CREATE TABLE app.artifacts (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES app.task_runs(id),
    event_id uuid,
    private_storage_key text NOT NULL UNIQUE,
    sha256 char(64) NOT NULL,
    mime_type varchar(128) NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    deleted_at timestamptz,
    UNIQUE(id,run_id),
    FOREIGN KEY(event_id,run_id) REFERENCES app.agent_events(id,run_id),
    CHECK (expires_at > created_at)
);
CREATE INDEX artifacts_expiry ON app.artifacts(expires_at) WHERE deleted_at IS NULL;

CREATE TABLE app.observations (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES app.task_runs(id),
    session_id uuid NOT NULL,
    epoch bigint NOT NULL CHECK (epoch > 0),
    frame_revision bigint NOT NULL CHECK (frame_revision >= 0),
    artifact_id uuid NOT NULL,
    captured_at timestamptz NOT NULL,
    width integer NOT NULL CHECK (width > 0),
    height integer NOT NULL CHECK (height > 0),
    UNIQUE(id,run_id),
    FOREIGN KEY(session_id,run_id) REFERENCES app.desktop_sessions(id,run_id),
    FOREIGN KEY(artifact_id,run_id) REFERENCES app.artifacts(id,run_id)
);

CREATE TABLE app.action_attempts (
    id uuid PRIMARY KEY, -- id de propuesta: redelivery no crea otra acción
    run_id uuid NOT NULL REFERENCES app.task_runs(id),
    session_id uuid NOT NULL,
    observation_id uuid NOT NULL,
    epoch bigint NOT NULL CHECK (epoch > 0),
    kind varchar(32) NOT NULL,
    risk varchar(16) NOT NULL CHECK (risk IN ('navigation','edit','commit','export')),
    sanitized_arguments jsonb NOT NULL,
    expected_effect text NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('proposed','rejected','intent','sent','verified','uncertain')),
    proposed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    sent_at timestamptz,
    verified_at timestamptz,
    outcome_observation_id uuid,
    FOREIGN KEY(session_id,run_id) REFERENCES app.desktop_sessions(id,run_id),
    FOREIGN KEY(observation_id,run_id) REFERENCES app.observations(id,run_id),
    FOREIGN KEY(outcome_observation_id,run_id) REFERENCES app.observations(id,run_id),
    CHECK (expires_at > proposed_at),
    CHECK (status <> 'verified' OR (verified_at IS NOT NULL AND outcome_observation_id IS NOT NULL))
);
CREATE INDEX actions_run_time ON app.action_attempts(run_id,proposed_at);

CREATE TABLE app.outbox (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES app.tasks(id),
    event_type varchar(64) NOT NULL,
    created_at timestamptz NOT NULL,
    available_at timestamptz NOT NULL,
    claimed_by varchar(128),
    claimed_until timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    published_at timestamptz,
    UNIQUE(task_id,event_type)
);
CREATE INDEX outbox_pending ON app.outbox(available_at,created_at) WHERE published_at IS NULL;

CREATE TABLE app.service_heartbeats (
    component varchar(32) NOT NULL,
    instance_id varchar(128) NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    ready boolean NOT NULL,
    sanitized_details jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY(component,instance_id)
);

-- Permisos, bootstrap LangGraph y triggers/guards se especifican en postgresql.md.
-- Constraints intra-fila no sustituyen la validación transaccional del cierre de sesión.
