"""Local-only evaluator state, safe output and reports. No desktop/model transport."""

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ("PASS", "FAIL", "ERROR", "SKIPPED")
ERROR_CODES = frozenset({
    "invalid_cli", "invalid_config", "missing_credentials", "desktop_target_mismatch",
    "invalid_fixture", "duplicate_fixture", "no_fixtures", "invalid_selection",
    "invalid_repetitions", "invalid_protocol", "invalid_task_response", "invalid_check_response",
    "reset_unconfirmed", "desktop_not_ready", "service_not_ready", "api_request_failed",
    "desktop_request_failed", "task_timeout", "previous_task_not_confirmed_terminal",
    "exclusive_reservation_required", "desktop_locked", "invalid_lock_state", "state_write_failed",
    "report_must_be_private", "report_write_failed", "report_exists", "interrupted",
    "blocked_by_previous_case", "unknown_task_requires_owner_reconciliation",
    "no_pending_operation", "entrypoint_changed", "reconciliation_still_pending",
})


class HarnessError(RuntimeError):
    def __init__(self, code):
        super().__init__(code if code in ERROR_CODES else "invalid_protocol")


def utc_now():
    return datetime.now(UTC).isoformat()


def valid_case_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None and not (
        value.lower().startswith(("sk-", "pk-", "ftr_")))


def private_path(path):
    destination = Path(path).resolve()
    if not destination.is_relative_to((ROOT / ".private").resolve()):
        raise HarnessError("report_must_be_private")
    return destination


def atomic_json(path, payload, *, error="report_write_failed"):
    """Replace and fsync before the next remote effect; files are always mode 0600."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=True, allow_nan=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        raise HarnessError(error) from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class CaseReport:
    # First six fields retain the old constructor order. Missing timings are null, not zero.
    case_id: str
    passed: bool = False
    checks: dict[str, bool] = field(default_factory=dict)
    acceptance_seconds: float | None = None
    duration_seconds: float | None = None  # Alias of terminal_detected_seconds, not case wall time.
    error_code: str | None = None
    outcome: str = "ERROR"
    suite_id: str | None = None
    repetition: int = 1
    task_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    trace_url: str | None = None
    telemetry_status: str = "unavailable"
    cost_usd: float | None = None
    cost_status: str = "unavailable"
    evaluation_pass: bool | None = None
    public_state: str | None = None
    started_at: str | None = None
    accepted_at: str | None = None
    terminal_detected_at: str | None = None
    finished_at: str | None = None
    reset_duration_seconds: float | None = None
    desktop_ready_wait_seconds: float | None = None
    terminal_detected_seconds: float | None = None
    task_observation_seconds: float | None = None
    check_duration_seconds: float | None = None
    case_wall_seconds: float | None = None
    queue_wait_seconds: float | None = None
    task_duration_seconds: float | None = None
    run_duration_seconds: float | None = None
    closure_confirmed: bool | None = None
    closure_basis: str = "unknown"
    actions_after_close: int | None = None
    desktop_ready_basis: str = "unknown"
    app_checks: list[dict] = field(default_factory=list)


class DesktopGuard:
    """Repository-wide, per-desktop flock + crash journal. Does not fence external VNC."""
    def __init__(self, desktop_ws, entrypoint, *, directory=None):
        self.directory = private_path(directory or ROOT / ".private/evaluaciones/state")
        self.desktop_ws, self.entrypoint = desktop_ws, entrypoint
        self.fd = None
        self.key = None
        self.authority = None
        self.path = None

    def __enter__(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            key_path = self.directory / ".hmac-key"
            key_fd = os.open(key_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(key_fd, fcntl.LOCK_EX)
                secret = os.read(key_fd, 33)
                if not secret:
                    if any(path.suffix in {".json", ".lock"} for path in self.directory.iterdir()):
                        # A missing key must not remap old pending desktops to new journal names.
                        raise HarnessError("invalid_lock_state")
                    secret = os.urandom(32)
                    os.write(key_fd, secret)
                    os.fsync(key_fd)
                if len(secret) != 32:
                    raise HarnessError("invalid_lock_state")
            finally:
                os.close(key_fd)
            desktop = urlsplit(self.desktop_ws)
            # Conservative origin-level lock: different WSS paths cannot bypass the same desktop.
            identity = f"{desktop.hostname.lower()}:{desktop.port or 443}"
            self.key = hmac.new(secret, identity.encode(), hashlib.sha256).hexdigest()
            self.authority = hmac.new(secret, self.entrypoint.rstrip("/").encode(), hashlib.sha256).hexdigest()
            self.path = self.directory / (self.key + ".json")
            self.fd = os.open(self.directory / (self.key + ".lock"),
                              os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BlockingIOError:
            self.__exit__(None, None, None)
            raise HarnessError("desktop_locked") from None
        except HarnessError:
            self.__exit__(None, None, None)
            raise
        except Exception:
            self.__exit__(None, None, None)
            raise HarnessError("invalid_lock_state") from None

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def pending(self):
        if self.fd is None:
            raise HarnessError("desktop_locked")
        try:
            if not self.path.exists():
                return None
            data = json.loads(self.path.read_text())
            if data.get("version") != 1 or data.get("status") not in {"clear", "pending"}:
                raise HarnessError("invalid_lock_state")
            if data["status"] == "clear":
                return None
            UUID(data["operation_id"])
            if data.get("task_id") is not None:
                UUID(data["task_id"])
            if data.get("phase") not in {"reset_in_flight", "desktop_ready_wait", "post_in_flight", "task_pending"}:
                raise HarnessError("invalid_lock_state")
            return data
        except HarnessError:
            raise
        except Exception:
            raise HarnessError("invalid_lock_state") from None

    def assert_clear(self):
        if self.pending() is not None:
            raise HarnessError("previous_task_not_confirmed_terminal")

    def mark(self, phase, report):
        previous = self.pending()
        data = {"version": 1, "status": "pending", "phase": phase,
            "operation_id": previous["operation_id"] if previous else str(uuid4()),
            "authority": self.authority, "suite_id": report.suite_id,
            "case_id": report.case_id, "repetition": report.repetition,
            "task_id": report.task_id, "updated_at": utc_now()}
        atomic_json(self.path, data, error="state_write_failed")

    def clear(self):
        atomic_json(self.path, {"version": 1, "status": "clear", "updated_at": utc_now()},
                    error="state_write_failed")


@dataclass(frozen=True)
class TraceResolver:
    base_url: str | None = None
    project_id: str | None = None

    def resolve(self, task_id):
        """No SDK client, credentials, trace creation, cost query or ingestion wait."""
        try:
            from langfuse import Langfuse

            trace_id = Langfuse.create_trace_id(seed=str(UUID(task_id)))
        except Exception:
            return {"trace_id": None, "trace_url": None, "telemetry_status": "unavailable"}
        url = None
        # Only documented public origins can reach stdout; self-hosted endpoints stay undisclosed.
        if self.base_url in {"https://us.cloud.langfuse.com", "https://cloud.langfuse.com"} and (
            isinstance(self.project_id, str) and re.fullmatch(r"[a-z0-9]{8,64}|[0-9a-f-]{36}", self.project_id)
        ):
            url = f"{self.base_url}/project/{self.project_id}/traces/{trace_id}"
        return {"trace_id": trace_id, "trace_url": url, "telemetry_status": "pending"}


def metric_summary(reports):
    """Include ERROR/FAIL samples; count missing values. No unstable small-sample P95."""
    names = ("acceptance_seconds", "terminal_detected_seconds", "task_observation_seconds",
             "reset_duration_seconds", "desktop_ready_wait_seconds", "check_duration_seconds",
             "case_wall_seconds")
    attempted = [report for report in reports if report.outcome != "SKIPPED"]
    result = {}
    for name in names:
        values = [getattr(report, name) for report in attempted]
        present = [value for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
        result[name] = {"samples": len(present), "missing": len(values) - len(present),
            "median": statistics.median(present) if present else None,
            "maximum": max(present) if present else None}
    return result


class ReportWriter:
    def __init__(self, destination, plan, *, suite_id, event_path=None, details=False,
                 monotonic=time.monotonic):
        self.destination = private_path(destination)
        self.event_path = private_path(event_path) if event_path else None
        self.details_path = self.destination.with_suffix(".details.jsonl") if details else None
        if self.event_path == self.destination or self.event_path == self.details_path:
            raise HarnessError("report_must_be_private")
        reserved = (ROOT / ".private/evaluaciones/state").resolve()
        if any(path.is_relative_to(reserved) for path in (
            self.destination, self.event_path, self.details_path) if path is not None):
            raise HarnessError("report_must_be_private")
        self.now, self.start = monotonic, monotonic()
        self.plan, self.suite_id = plan, suite_id
        self.started_at = utc_now()
        self.reports = []
        self.current = None
        self.status, self.error_code = "running", None
        self.finished_at = None
        # Archive an earlier snapshot before replacing latest, so pending references are not lost.
        if self.destination.exists():
            previous = self.destination.parent / ("previous-" + uuid4().hex + ".json")
            try:
                os.link(self.destination, previous)
            except Exception:
                raise HarnessError("report_write_failed") from None
        for path in (self.event_path, self.details_path):
            if path and path.exists():
                raise HarnessError("report_exists")
        self.save()

    @staticmethod
    def append_jsonl(path, value):
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as output:
                output.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            raise HarnessError("report_write_failed") from None

    def event(self, event, report):
        self.current = asdict(report)
        if report.finished_at is None:
            self.current["outcome"] = None
        self.save()
        if self.event_path:
            self.append_jsonl(self.event_path, {"event": event, "timestamp": utc_now(),
                "suite_id": self.suite_id, "case_id": report.case_id, "repetition": report.repetition,
                "task_id": report.task_id, "state": report.public_state,
                "elapsed_seconds": report.task_observation_seconds,
                "outcome": report.outcome if event == "case_finished" else None,
                "error_code": report.error_code,
                "metrics": {name: getattr(report, name) for name in (
                    "reset_duration_seconds", "desktop_ready_wait_seconds", "acceptance_seconds",
                    "terminal_detected_seconds", "check_duration_seconds", "case_wall_seconds")}})

    def completed(self, report):
        self.reports.append(report)
        self.current = None
        self.save()

    def finish(self, status, error_code=None):
        self.status, self.error_code = status, error_code
        self.finished_at = utc_now()
        self.save()

    def save(self):
        counts = Counter(report.outcome for report in self.reports)
        payload = {"schema_version": 2, "suite_id": self.suite_id, "status": self.status,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "suite_wall_seconds": max(0, self.now() - self.start), "error_code": self.error_code,
            "total_planned": len(self.plan), "total_executed": sum(
                report.started_at is not None and report.outcome != "SKIPPED" for report in self.reports),
            "counts": {key: counts[key] for key in OUTCOMES}, "plan": self.plan,
            "current_case": self.current, "reports": [asdict(report) for report in self.reports],
            "metrics": metric_summary(self.reports),
            "metrics_by_case": {case_id: metric_summary([
                report for report in self.reports if report.case_id == case_id])
                for case_id in dict.fromkeys(entry["case_id"] for entry in self.plan)}}
        atomic_json(self.destination, payload)


class ConsoleProgress:
    def __init__(self, repetitions, *, write=None):
        self.repetitions = repetitions
        self.write = write or (lambda message: print(message, flush=True))

    def __call__(self, event, report):
        prefix = f"[{report.case_id} {report.repetition}/{self.repetitions}]"
        if event == "reset_started":
            self.write(prefix + " Preparando escritorio")
        elif event == "task_accepted":
            self.write(prefix + f" Aceptada en {report.acceptance_seconds:.2f} s | task_id={report.task_id}")
        elif event == "poll":
            self.write(prefix + f" {report.public_state} | detección {report.task_observation_seconds:.1f} s")
        elif event == "case_finished":
            checks = " | ".join(f"{name}={'OK' if ok else 'FAIL'}" for name, ok in report.checks.items())
            self.write(prefix + f" {report.outcome}" + (" | " + checks if checks else "") +
                       (" | " + report.error_code if report.error_code else ""))
            if report.task_id:
                reference = report.trace_url or ("trace_id=" + report.trace_id if report.trace_id else
                                                 "task_id=" + report.task_id)
                self.write(prefix + " Langfuse: " + reference + " | " + report.telemetry_status)


def private_text(value, secrets):
    text = value[:16384]
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?:https?|wss?|postgresql(?:\+psycopg)?|redis)://\S+|"
                  r"(?:sk-|pk-|ftr_)[A-Za-z0-9_-]+|Bearer\s+\S+|data:image/\S+", "[redacted]", text)
    return text
