"""Real HTTP practice evaluator; list/dry-run are offline, execution is opt-in.

Fixtures and expectations remain harness-only. Terminal detection is not wire closure.
"""
import argparse
import asyncio
import json
import math
import os
import re
import signal
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values

if __package__:
    from . import evaluation_support as support
else:
    import evaluation_support as support

ROOT = support.ROOT
HarnessError = support.HarnessError
CaseReport = support.CaseReport


@dataclass(repr=False, frozen=True)
class PracticeConfig:
    entrypoint: str
    desktop_base: str
    desktop_ws: str
    password: str
    desktop_token: str
    team_token: str
    ca_file: Path | None = None

    def validate(self):
        def valid_http(value):
            url = urlsplit(value)
            return (url.scheme == "https" and url.hostname and not url.username
                    and not url.password and not url.query and not url.fragment)

        try:
            desktop, ws = urlsplit(self.desktop_base), urlsplit(self.desktop_ws)
            if not valid_http(self.entrypoint) or not valid_http(self.desktop_base):
                raise HarnessError("invalid_config")
            if (ws.scheme != "wss" or not ws.hostname or ws.username or ws.password
                    or ws.query or ws.fragment or ws.hostname != desktop.hostname
                    or (ws.port or 443) != (desktop.port or 443)):
                raise HarnessError("desktop_target_mismatch")
            # Validate all ports before any request, including the entrypoint.
            urlsplit(self.entrypoint).port
            if not all((self.password, self.desktop_token, self.team_token)):
                raise HarnessError("missing_credentials")
        except (ValueError, TypeError):
            raise HarnessError("invalid_config") from None


def client_options(config):
    """Keep public CAs and add an explicit local development CA; never verify=False."""
    options = {"trust_env": False, "follow_redirects": False}
    if config.ca_file is not None:
        try:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=str(config.ca_file))
        except (OSError, ssl.SSLError):
            raise HarnessError("invalid_config") from None
        options["verify"] = context
    return options


def _numeric(value: str):
    try:
        number = Decimal(value)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def summary_checks(fixture: dict, state: str, summary: str) -> dict[str, bool]:
    checks = {"expected_state": state == fixture["estado_esperado"]}
    reason_keys = fixture.get("claves_razon", [])
    if reason_keys:
        checks["reason_identifiers"] = all(str(key) in summary for key in reason_keys)
    expected = fixture.get("respuesta_esperada")
    lines = re.findall(r"^respuesta:[ \t]*(.*)$", summary, re.IGNORECASE | re.MULTILINE)
    if state == "fallida":
        checks["no_invented_answer"] = re.search(r"^[ \t]*respuesta:", summary, re.I | re.M) is None
    if expected is None:
        return checks
    checks["answer_format"] = len(lines) == 1
    if len(lines) != 1:
        checks["answer_value"] = False
        return checks
    expected_parts = expected if isinstance(expected, list) else str(expected).split(";")
    actual_parts = lines[0].split(";")
    checks["answer_format"] = len(actual_parts) == len(expected_parts)
    matches = []
    for wanted, actual in zip(expected_parts, actual_parts, strict=False):
        number = _numeric(str(wanted).strip())
        if number is not None:
            first = re.search(r"[-+]?\d+(?:[.,]\d+)?", actual)
            observed = _numeric(first.group().replace(",", ".")) if first else None
            matches.append(observed == number)
        else:
            matches.append(actual.strip().casefold() == str(wanted).strip().casefold())
    checks["answer_value"] = checks["answer_format"] and all(matches)
    return checks


def validate_fixture(fixture: dict) -> None:
    if (not isinstance(fixture, dict) or not support.valid_case_id(fixture.get("id"))
            or not isinstance(fixture.get("instruccion"), str)
            or not fixture["instruccion"].strip()
            or len(fixture["instruccion"].encode()) > 16384
            or not isinstance(fixture.get("estado_esperado"), str)
            or fixture["estado_esperado"] not in {"terminada", "fallida"}):
        raise HarnessError("invalid_fixture")
    if any(not isinstance(fixture.get(name, []), list) for name in ("claves_razon", "diff_esperado", "lista_blanca")):
        raise HarnessError("invalid_fixture")
    if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in fixture.get("claves_razon", [])):
        raise HarnessError("invalid_fixture")
    expected = fixture.get("respuesta_esperada")
    values = expected if isinstance(expected, list) else [expected]
    if expected is not None and (not values or any(
        not isinstance(value, (str, int, float)) or isinstance(value, bool)
        or (isinstance(value, float) and not math.isfinite(value)) for value in values
    )):
        raise HarnessError("invalid_fixture")


def load_fixtures(directory):
    fixtures = []
    try:
        for path in sorted(directory.glob("*.json")):
            if path.stat().st_size > 131072:
                raise HarnessError("invalid_fixture")
            fixture = json.loads(path.read_text())
            validate_fixture(fixture)
            fixtures.append(fixture)
    except HarnessError:
        raise
    except Exception:
        raise HarnessError("invalid_fixture") from None
    if not fixtures:
        raise HarnessError("no_fixtures")
    if len({fixture["id"] for fixture in fixtures}) != len(fixtures):
        raise HarnessError("duplicate_fixture")
    return fixtures


def select_fixtures(fixtures, cases=None, all_cases=False):
    if not fixtures or bool(cases is not None) == bool(all_cases) or (
        cases is not None and not isinstance(cases, str)
    ):
        raise HarnessError("invalid_selection")
    if all_cases:
        return list(fixtures)
    selected = [value.strip() for value in cases.split(",")]
    by_id = {fixture["id"]: fixture for fixture in fixtures}
    if (not selected or any(not value or value not in by_id for value in selected)
            or len(selected) != len(set(selected))):
        raise HarnessError("invalid_selection")
    return [by_id[value] for value in selected]


def validate_repetitions(repetitions):
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise HarnessError("invalid_repetitions")


class PracticeHarness:
    def __init__(self, config: PracticeConfig, client: httpx.AsyncClient, *,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable = asyncio.sleep, poll_seconds: float = 5,
                 task_seconds: float = 300, close_target_seconds: float = 285,
                 exclusive_reserved=False, guard=None, on_event=None, on_report=None,
                 trace_resolver=None, utc_now=support.utc_now,
                 desktop_ready_timeout_seconds=20, desktop_settle_seconds=2,
                 private_details=None):
        config.validate()
        self.config, self.client = config, client
        self.now, self.sleep = monotonic, sleep
        self.poll_seconds, self.task_seconds = poll_seconds, task_seconds
        self.close_target_seconds = close_target_seconds
        # This flag means no local task is pending, never evidence of desktop ownership.
        self.safe_to_reset = True
        self.exclusive_reserved, self.guard = exclusive_reserved, guard
        self.on_event, self.on_report = on_event, on_report
        self.trace_resolver = trace_resolver or support.TraceResolver()
        self.utc_now = utc_now
        self.desktop_ready_timeout_seconds = desktop_ready_timeout_seconds
        self.desktop_settle_seconds = desktop_settle_seconds
        self.private_details = private_details
        self.stop_requested = False
        self.current_report = None
        self.suite_id = None
        for value in (poll_seconds, task_seconds, desktop_ready_timeout_seconds):
            if not math.isfinite(value) or value <= 0:
                raise HarnessError("invalid_config")
        if not 0 <= desktop_settle_seconds < desktop_ready_timeout_seconds:
            raise HarnessError("invalid_config")

    async def _request(self, method: str, path: str, *, desktop=False, payload=None, timeout=10):
        base = self.config.desktop_base if desktop else self.config.entrypoint
        token = self.config.desktop_token if desktop else self.config.team_token
        try:
            async with asyncio.timeout(min(10, timeout)):
                response = await self.client.request(method, base.rstrip("/") + path,
                    headers={"Authorization": "Bearer " + token}, json=payload,
                    timeout=min(10, timeout), follow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 131072:
                raise HarnessError("invalid_protocol")
            return response.json()
        except Exception:  # noqa: BLE001 — transport errors may contain URL and credentials.
            raise HarnessError("desktop_request_failed" if desktop else "api_request_failed") from None

    def emit(self, event, report):
        if self.on_event:
            self.on_event(event, report)

    def require_reservation(self):
        if not self.exclusive_reserved:
            raise HarnessError("exclusive_reservation_required")
        if self.guard:
            self.guard.assert_clear()

    async def confirm_service(self):
        health = await self._request("GET", "/health")
        if not isinstance(health, dict) or health.get("ok") is not True:
            raise HarnessError("service_not_ready")

    async def wait_desktop_ready(self):
        start = self.now()
        deadline = start + self.desktop_ready_timeout_seconds
        while self.now() < deadline:
            try:
                health = await self._request("GET", "/health", desktop=True, timeout=deadline - self.now())
                ready = isinstance(health, dict) and health.get("ok") is True
            except HarnessError:
                ready = False
            if ready:
                if self.now() + self.desktop_settle_seconds >= deadline:
                    break
                await self.sleep(self.desktop_settle_seconds)
                if self.now() < deadline:
                    return
            await self.sleep(min(0.5, max(0, deadline - self.now())))
        raise HarnessError("desktop_not_ready")

    @staticmethod
    def task_result(payload):
        if not isinstance(payload, dict):
            raise HarnessError("invalid_task_response")
        state, summary = payload.get("estado"), payload.get("resumen")
        if (not isinstance(state, str) or state not in {"en_curso", "terminada", "fallida"}
                or not isinstance(summary, str) or len(summary) > 32768):
            raise HarnessError("invalid_task_response")
        return state, summary

    @staticmethod
    def app_result(payload):
        if not isinstance(payload, dict) or type(payload.get("pass")) is not bool:
            raise HarnessError("invalid_check_response")
        rows = payload.get("checks")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise HarnessError("invalid_check_response")
        canonical = {"cambios esperados": "expected_changes", "sin acciones fuera de la tarea": "within_scope",
                     "sin registros duplicados": "no_duplicates"}
        checks = []
        for index, item in enumerate(rows, 1):
            if (not isinstance(item, dict) or type(item.get("ok")) is not bool
                    or not isinstance(item.get("nombre"), str) or not item["nombre"].strip()):
                raise HarnessError("invalid_check_response")
            # Provider names are untrusted data: unknown labels are replaced by a stable index.
            name = canonical.get(item["nombre"].casefold(), f"check_{index:03d}")
            checks.append({"name": name, "ok": item["ok"]})
        all_ok = all(item["ok"] for item in checks)
        if payload["pass"] != all_ok:
            raise HarnessError("invalid_check_response")
        return all_ok, checks

    async def run_case(self, fixture: dict, *, repetition=1, suite_id=None) -> CaseReport:
        validate_fixture(fixture)
        self.require_reservation()
        if not self.safe_to_reset:
            raise HarnessError("previous_task_not_confirmed_terminal")
        report = CaseReport(fixture["id"], suite_id=suite_id or str(uuid4()), repetition=repetition)
        start, posted_at = self.now(), None
        self.current_report = report
        report.started_at = self.utc_now()
        try:
            if self.stop_requested:
                report.outcome, report.error_code = "SKIPPED", "interrupted"
                return report
            await self.confirm_service()
            self.safe_to_reset = False
            if self.guard:
                self.guard.mark("reset_in_flight", report)
            self.emit("reset_started", report)
            reset_start = self.now()
            try:
                reset = await self._request("POST", "/__reset", desktop=True, payload={})
            finally:
                report.reset_duration_seconds = max(0, self.now() - reset_start)
            if not isinstance(reset, dict) or reset.get("ok") is not True:
                raise HarnessError("reset_unconfirmed")
            if self.guard:
                self.guard.mark("desktop_ready_wait", report)
            ready_start = self.now()
            try:
                await self.wait_desktop_ready()
            finally:
                report.desktop_ready_wait_seconds = max(0, self.now() - ready_start)
            report.desktop_ready_basis = "desktop_health_plus_settle"
            self.emit("desktop_ready", report)
            if self.private_details:
                self.private_details(report, "instruction", fixture["instruccion"])
            if self.stop_requested:
                raise HarnessError("interrupted")
            if self.guard:
                self.guard.mark("post_in_flight", report)
            self.emit("task_submitting", report)
            posted_at = self.now()
            deadline = posted_at + self.task_seconds
            try:
                accepted = await self._request("POST", "/tasks", payload={
                    "tarea": fixture["instruccion"], "escritorio": {
                        "url": self.config.desktop_ws, "password": self.config.password,
                    },
                }, timeout=self.task_seconds)
            finally:
                report.acceptance_seconds = max(0, self.now() - posted_at)
            report.task_id = str(UUID(accepted["task_id"]))
            report.accepted_at = self.utc_now()
            if self.guard:
                self.guard.mark("task_pending", report)
            # Persist acceptance BEFORE best-effort trace correlation or any further await.
            self.emit("task_accepted", report)
            try:
                correlation = self.trace_resolver.resolve(report.task_id)
                trace_id = correlation.get("trace_id")
                if isinstance(trace_id, str) and re.fullmatch(r"[0-9a-f]{32}", trace_id):
                    report.trace_id = trace_id
                    report.telemetry_status = "pending"
                    url = correlation.get("trace_url")
                    if isinstance(url, str) and re.fullmatch(
                        r"https://(?:us\.)?cloud\.langfuse\.com/project/[a-z0-9-]{8,64}/traces/" + trace_id, url
                    ):
                        report.trace_url = url
            except Exception:
                # Correlation/ingestion is optional and must not abandon an accepted task.
                report.telemetry_status = "unavailable"
            self.emit("trace_correlated", report)
            while self.now() < deadline:
                await self.sleep(min(self.poll_seconds, max(0, deadline - self.now())))
                remaining = deadline - self.now()
                if remaining <= 0:
                    break
                try:
                    result = await self._request("GET", "/tasks/" + report.task_id, timeout=remaining)
                except HarnessError:
                    if self.now() >= deadline:
                        report.checks["terminal_within_300s"] = False
                        raise HarnessError("task_timeout") from None
                    raise
                if self.now() >= deadline:
                    break
                state, summary = self.task_result(result)
                report.public_state = state
                report.task_observation_seconds = max(0, self.now() - posted_at)
                if state != "en_curso":
                    report.terminal_detected_seconds = report.task_observation_seconds
                    report.duration_seconds = report.terminal_detected_seconds
                    report.terminal_detected_at = self.utc_now()
                self.emit("poll", report)
                if state == "en_curso":
                    continue
                # Terminal declares closure by API contract; physical wire evidence stays unknown.
                report.closure_basis = "api_terminal_contract"
                if self.guard:
                    self.guard.clear()
                self.safe_to_reset = True
                report.checks = summary_checks(fixture, state, summary)
                report.checks.update({"accepted_under_2s": report.acceptance_seconds < 2,
                    "terminal_within_300s": report.terminal_detected_seconds < self.task_seconds,
                    "closure_contract": True})
                if self.private_details:
                    self.private_details(report, "summary", summary)
                self.emit("check_started", report)
                check_start = self.now()
                try:
                    app = await self._request("POST", "/__check", desktop=True, payload=fixture)
                finally:
                    report.check_duration_seconds = max(0, self.now() - check_start)
                report.checks["app_state"], report.app_checks = self.app_result(app)
                report.passed = all(report.checks.values())
                report.evaluation_pass = report.passed
                report.outcome = "PASS" if report.passed else "FAIL"
                return report
            report.checks["terminal_within_300s"] = False
            raise HarnessError("task_timeout")
        except asyncio.CancelledError:
            self.stop_requested = True
            report.error_code = "interrupted"
            return report
        except HarnessError as exc:
            report.error_code = str(exc)
            if self.safe_to_reset or str(exc) in {"report_write_failed", "state_write_failed", "interrupted"}:
                self.stop_requested = True
            return report
        except Exception:  # noqa: BLE001 — never expose response data or credentials.
            report.error_code = "invalid_protocol"
            self.stop_requested = True
            return report
        finally:
            if posted_at is not None and report.terminal_detected_seconds is None:
                report.task_observation_seconds = max(0, self.now() - posted_at)
            report.case_wall_seconds = max(0, self.now() - start)
            report.finished_at = self.utc_now()
            self.emit("case_finished", report)

    async def run_suite(self, fixtures: list[dict], repetitions: int = 3, *, suite_id=None) -> list[CaseReport]:
        # Preflight the entire suite before making any destructive reset.
        for fixture in fixtures:
            validate_fixture(fixture)
        validate_repetitions(repetitions)
        if not fixtures or len({fixture["id"] for fixture in fixtures}) != len(fixtures):
            raise HarnessError("invalid_selection")
        self.require_reservation()
        self.suite_id = suite_id or str(uuid4())
        reports = []
        for repetition in range(1, repetitions + 1):
            for fixture in fixtures:
                if self.stop_requested or not self.safe_to_reset:
                    report = CaseReport(fixture["id"], outcome="SKIPPED", suite_id=self.suite_id,
                        repetition=repetition, error_code="interrupted" if self.stop_requested else "blocked_by_previous_case")
                    self.emit("case_finished", report)
                else:
                    report = await self.run_case(fixture, repetition=repetition, suite_id=self.suite_id)
                reports.append(report)
                if self.on_report:
                    self.on_report(report)
                self.current_report = None
        return reports


def load_config(env_file: Path, entrypoint: str | None) -> PracticeConfig:
    # dotenv is consumed locally. Never print its values, config repr or exceptions.
    values = {**dotenv_values(env_file, interpolate=False), **os.environ}
    return PracticeConfig(entrypoint=entrypoint or values.get("TEAM_ENTRYPOINT_URL") or "",
        desktop_base=values.get("VNC_BASE_URL") or "",
        desktop_ws=values.get("VNC_WS_URL") or "",
        password=values.get("VNC_PASSWORD") or "",
        desktop_token=values.get("VNC_DESKTOP_TOKEN") or "",
        team_token=values.get("TEAM_ENTRYPOINT_TOKEN") or "")


def load_trace_resolver(env_file=None):
    values = {**(dotenv_values(env_file, interpolate=False) if env_file else {}), **os.environ}
    base = (values.get("LANGFUSE_BASE_URL") or "").rstrip("/")
    return support.TraceResolver(base or None, values.get("LANGFUSE_PROJECT_ID"))


async def reconcile(config, guard):
    pending = guard.pending()
    if pending is None:
        raise HarnessError("no_pending_operation")
    if pending.get("authority") != guard.authority:
        raise HarnessError("entrypoint_changed")
    if not pending.get("task_id"):
        print("Operación pendiente: operation_id=" + str(UUID(pending["operation_id"])))
        raise HarnessError("unknown_task_requires_owner_reconciliation")
    task_id = str(UUID(pending["task_id"]))
    # Read-only API reconciliation: never reset, submit, cancel or call __check.
    async with httpx.AsyncClient(**client_options(config)) as client:
        harness = PracticeHarness(config, client, exclusive_reserved=True)
        await harness.confirm_service()
        result = await harness._request("GET", "/tasks/" + task_id)
        state, _ = harness.task_result(result)
        if state == "en_curso":
            raise HarnessError("reconciliation_still_pending")
        record = {"task_id": task_id, "public_state": state, "timestamp": support.utc_now(),
                  "closure_basis": "api_terminal_contract", "closure_confirmed": None}
        support.atomic_json(guard.directory / ("reconciled-" + uuid4().hex + ".json"), record)
        guard.clear()
    print(f"Reconciliada por contrato terminal: task_id={task_id} | {state}. No se reseteó el escritorio.")
    return 0


def record_remaining(writer, harness, plan, error_code):
    completed = {(report.case_id, report.repetition) for report in writer.reports}
    current = harness.current_report
    if current and (current.case_id, current.repetition) not in completed:
        current.outcome, current.passed, current.error_code = "ERROR", False, error_code
        current.evaluation_pass = None
        current.finished_at = support.utc_now()
        writer.completed(current)
        completed.add((current.case_id, current.repetition))
    for entry in plan:
        if (entry["case_id"], entry["repetition"]) not in completed:
            writer.completed(CaseReport(entry["case_id"], outcome="SKIPPED", suite_id=writer.suite_id,
                repetition=entry["repetition"], error_code="interrupted" if error_code == "interrupted" else "blocked_by_previous_case"))


async def main(args) -> int:
    validate_repetitions(args.repetitions)
    if args.reconcile:
        if args.cases is not None or args.all or not args.exclusive_desktop:
            raise HarnessError("invalid_cli")
        config = load_config(args.env_file, args.entrypoint)
        config = replace(config, ca_file=args.ca_file)
        config.validate()
        with support.DesktopGuard(config.desktop_ws, config.entrypoint) as guard:
            return await reconcile(config, guard)

    # Entire fixture catalog/selection is validated BEFORE credentials, locks or any HTTP.
    fixtures = load_fixtures(args.fixtures)
    if args.list:
        if args.cases is not None or args.all:
            raise HarnessError("invalid_selection")
        for fixture in fixtures:
            print(fixture["id"])
        return 0
    selected = select_fixtures(fixtures, args.cases, args.all)
    plan = [{"case_id": fixture["id"], "repetition": repetition}
            for repetition in range(1, args.repetitions + 1) for fixture in selected]
    if args.dry_run:
        for entry in plan:
            print(f"[{entry['case_id']} {entry['repetition']}/{args.repetitions}] Plan (sin red)")
        print(f"Suite planificada: {len(plan)} casos. No se leyó configuración privada.")
        return 0
    if not args.exclusive_desktop:
        raise HarnessError("exclusive_reservation_required")
    for value in (args.desktop_ready_timeout, args.desktop_settle_seconds):
        if not math.isfinite(value):
            raise HarnessError("invalid_config")
    if not 0 <= args.desktop_settle_seconds < args.desktop_ready_timeout <= 60:
        raise HarnessError("invalid_config")
    destination = support.private_path(args.report)
    if args.events:
        support.private_path(args.events)
    config = load_config(args.env_file, args.entrypoint)
    config = replace(config, ca_file=args.ca_file)
    config.validate()
    options = client_options(config)
    resolver = load_trace_resolver(args.langfuse_env_file)
    suite_id = str(uuid4())
    with support.DesktopGuard(config.desktop_ws, config.entrypoint) as guard:
        pending = guard.pending()
        if pending:
            if pending.get("task_id"):
                print("Bloqueo persistido: task_id=" + str(UUID(pending["task_id"])))
            raise HarnessError("previous_task_not_confirmed_terminal")
        writer = support.ReportWriter(destination, plan, suite_id=suite_id,
            event_path=args.events, details=args.private_details)
        console = support.ConsoleProgress(args.repetitions)

        def event(name, report):
            writer.event(name, report)
            console(name, report)

        def details(report, kind, text):
            secrets = (config.password, config.desktop_token, config.team_token,
                       config.desktop_base, config.desktop_ws, config.entrypoint)
            writer.append_jsonl(writer.details_path, {"suite_id": suite_id, "case_id": report.case_id,
                "repetition": report.repetition, "kind": kind, "text": support.private_text(text, secrets)})

        async with httpx.AsyncClient(**options) as client:
            harness = PracticeHarness(config, client, guard=guard, exclusive_reserved=True,
                on_event=event, on_report=writer.completed, trace_resolver=resolver,
                desktop_ready_timeout_seconds=args.desktop_ready_timeout,
                desktop_settle_seconds=args.desktop_settle_seconds,
                private_details=details if args.private_details else None)
            loop = asyncio.get_running_loop()
            current_task = asyncio.current_task()
            installed, stopped_by = [], []

            def interrupt(sig):
                if not stopped_by:
                    stopped_by.append(sig)
                    harness.stop_requested = True
                    current_task.cancel()

            try:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, interrupt, sig)
                    installed.append(sig)
                print("Reserva exclusiva declarada. El lock local no impide clientes VNC externos.")
                reports = await harness.run_suite(selected, args.repetitions, suite_id=suite_id)
                first_error = next((report.error_code for report in reports if report.outcome == "ERROR"), None)
                status = ("interrupted" if stopped_by or first_error == "interrupted" else
                          "blocked" if not harness.safe_to_reset else "error" if first_error else "completed")
                writer.finish(status, "interrupted" if stopped_by else first_error)
            except asyncio.CancelledError:
                record_remaining(writer, harness, plan, "interrupted")
                writer.finish("interrupted", "interrupted")
                reports = writer.reports
            except Exception as exc:
                code = str(exc) if isinstance(exc, HarnessError) else "invalid_protocol"
                record_remaining(writer, harness, plan, code)
                writer.finish("error", code)
                print("Suite detenida: " + code)
                return 2
            finally:
                for sig in installed:
                    loop.remove_signal_handler(sig)
        counts = {outcome: sum(report.outcome == outcome for report in reports) for outcome in support.OUTCOMES}
        print(f"Suite: {counts['PASS']}/{len(plan)} PASS | {counts['FAIL']} FAIL | "
              f"{counts['ERROR']} ERROR | {counts['SKIPPED']} SKIPPED. Reporte privado guardado.")
        if not harness.safe_to_reset:
            print("Bloqueo conservado por escritorio; cerrar HTTP no cancela la tarea remota.")
        if stopped_by:
            return 128 + int(stopped_by[0])
        return 0 if counts["PASS"] == len(plan) else 2 if counts["ERROR"] else 1


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes untrusted CLI values; only a fixed code escapes.
        raise HarnessError("invalid_cli")


def build_parser():
    parser = SafeArgumentParser(description="Evaluador de práctica: planificación offline o HTTP real explícito")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--list", action="store_true", help="List IDs without credentials or network")
    modes.add_argument("--dry-run", action="store_true", help="Print selection plan without network")
    modes.add_argument("--execute", action="store_true", help="Reset and evaluate selected cases over real HTTP")
    modes.add_argument("--reconcile", action="store_true", help="Read pending task status; never reset or submit")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--cases", help="Comma-separated case IDs, in execution order")
    selection.add_argument("--all", action="store_true", help="Explicitly select every fixture")
    parser.add_argument("--entrypoint", help="HTTPS URL; alternatively TEAM_ENTRYPOINT_URL in local config")
    parser.add_argument("--ca-file", type=Path, help="Additional trusted CA for a local HTTPS API; TLS verification stays enabled")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.vnc")
    parser.add_argument("--langfuse-env-file", type=Path, help="Optional local project/base-URL config; no tracing keys needed")
    parser.add_argument("--exclusive-desktop", action="store_true", help="Declare externally coordinated exclusive reservation")
    parser.add_argument("--fixtures", type=Path, default=ROOT.parent / "frontier-reto-1/pruebas")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--report", type=Path, default=ROOT / ".private/evaluaciones/latest.json")
    parser.add_argument("--events", type=Path, help="Optional private JSONL progress file")
    parser.add_argument("--private-details", action="store_true", help="Save redacted instruction/summary in separate private JSONL")
    parser.add_argument("--desktop-ready-timeout", type=float, default=20)
    parser.add_argument("--desktop-settle-seconds", type=float, default=2)
    return parser


def cli(argv=None):
    try:
        return asyncio.run(main(build_parser().parse_args(argv)))
    except KeyboardInterrupt:
        print("Evaluación interrumpida; no se canceló ninguna tarea remota.")
        return 130
    except Exception as exc:
        code = str(exc) if isinstance(exc, HarnessError) else "invalid_config"
        print("Evaluación detenida: " + code + ". Detalles privados omitidos.")
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
