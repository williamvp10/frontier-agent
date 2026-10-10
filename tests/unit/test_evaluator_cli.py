"""Evaluator acceptance: synthetic fixtures, mock HTTP and isolated journals.

Never reads organizer expectations or credentials and never operates real VNC.
"""

import asyncio
import json
import stat
from dataclasses import asdict, replace
from uuid import uuid4

import httpx
import pytest

from scripts import evaluate
from scripts import evaluation_support as support
from tests.unit.test_harness import CONFIG, FIXTURE, Clock, Service

pytestmark = pytest.mark.unit


@pytest.fixture
def private_root(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "ROOT", tmp_path)
    return tmp_path / ".private" / "evaluaciones"


@pytest.fixture
def fixtures(tmp_path):
    # Different filenames demonstrate stable catalog order vs explicit selection order.
    folder = tmp_path / "synthetic-fixtures"
    folder.mkdir()
    for name, case in (("01.json", "E1"), ("02.json", "E3")):
        (folder / name).write_text(json.dumps({**FIXTURE, "id": case}))
    return folder


def forbid_private_or_network(*args, **kwargs):
    raise AssertionError("Offline CLI must not load config, acquire a lock or use HTTP")


@pytest.mark.parametrize("mode,selection,expected", [
    ("--list", [], ["E1", "E3"]),
    ("--dry-run", ["--cases", "E3,E1"], ["E3", "E1", "E3", "E1"]),
    ("--dry-run", ["--all"], ["E1", "E3", "E1", "E3"]),
])
def test_offline_cli_selection_order_without_credentials_network_or_journals(
    fixtures, monkeypatch, capsys, mode, selection, expected
):
    monkeypatch.setattr(evaluate, "load_config", forbid_private_or_network)
    monkeypatch.setattr(evaluate, "load_trace_resolver", forbid_private_or_network)
    monkeypatch.setattr(support, "DesktopGuard", forbid_private_or_network)
    monkeypatch.setattr(evaluate.httpx, "AsyncClient", forbid_private_or_network)
    code = evaluate.cli([mode, "--fixtures", str(fixtures), "--repetitions", "2", *selection])
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    if mode == "--list":
        assert lines == expected
    else:
        assert [line.split()[0].removeprefix("[") for line in lines[:-1]] == expected
        assert "No se leyó configuración privada" in lines[-1]
    assert "Count visible rows" not in "\n".join(lines)


@pytest.mark.parametrize("arguments", [
    ["--dry-run"], ["--dry-run", "--cases", ""], ["--dry-run", "--cases", "E9"],
    ["--dry-run", "--cases", "E1,E1"], ["--dry-run", "--all", "--cases", "E1"],
    ["--dry-run", "--all", "--repetitions", "0"],
    ["--dry-run", "--all", "--repetitions", "11"],
    ["--execute", "--cases", "E1"], ["--execute", "--exclusive-desktop"],
    ["--list", "--cases", "E1"],
])
def test_invalid_selection_or_missing_reservation_rejects_before_private_config(
    fixtures, monkeypatch, arguments, capsys
):
    monkeypatch.setattr(evaluate, "load_config", forbid_private_or_network)
    assert evaluate.cli([*arguments, "--fixtures", str(fixtures)]) == 2
    assert "Evaluación detenida:" in capsys.readouterr().out


def test_cli_parser_and_invalid_fixture_errors_never_echo_canaries(fixtures, capsys):
    assert evaluate.cli(["--unrecognized", "CANARY_TEAM_TOKEN"]) == 2
    assert "CANARY_TEAM_TOKEN" not in capsys.readouterr().out
    (fixtures / "03.json").write_text(json.dumps({**FIXTURE, "id": "sk-CANARY_PRIVATE_SECRET"}))
    assert evaluate.cli(["--list", "--fixtures", str(fixtures)]) == 2
    assert "CANARY_PRIVATE_SECRET" not in capsys.readouterr().out


def test_guard_locks_same_desktop_origin_across_paths_and_persists_pending(private_root):
    directory = private_root / "state"
    report = support.CaseReport("E1", suite_id=str(uuid4()), repetition=1)
    with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=directory) as first:
        first.mark("post_in_flight", report)
        assert stat.S_IMODE(first.path.stat().st_mode) == 0o600
        assert CONFIG.desktop_ws not in first.path.name
        with pytest.raises(support.HarnessError, match="desktop_locked"):
            with support.DesktopGuard("wss://desktop.example/other", CONFIG.entrypoint, directory=directory):
                pass
    with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=directory) as restarted:
        assert restarted.pending()["phase"] == "post_in_flight"
        with pytest.raises(support.HarnessError, match="previous_task_not_confirmed_terminal"):
            restarted.assert_clear()
        content = restarted.path.read_text()
        assert all(value not in content for value in (CONFIG.desktop_ws, CONFIG.password, CONFIG.team_token))


def test_missing_hmac_key_cannot_bypass_previous_journal(private_root):
    directory = private_root / "state"
    with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=directory) as guard:
        guard.mark("reset_in_flight", support.CaseReport("E1"))
    (directory / ".hmac-key").unlink()
    with pytest.raises(support.HarnessError, match="invalid_lock_state"):
        with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=directory):
            pass


async def test_uncertain_post_survives_new_harness_and_prevents_any_other_request(private_root):
    clock, service = Clock(), Service(uncertain_accept=True)
    directory = private_root / "state"
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=directory) as guard:
            harness = evaluate.PracticeHarness(CONFIG, client, guard=guard, exclusive_reserved=True,
                monotonic=clock.now, sleep=clock.sleep)
            reports = await harness.run_suite([{**FIXTURE, "id": "E1"}, {**FIXTURE, "id": "E3"}], 1)
            assert [report.outcome for report in reports] == ["ERROR", "SKIPPED"]
            assert guard.pending()["phase"] == "post_in_flight" and guard.pending()["task_id"] is None
        calls = len(service.requests)
        with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=directory) as guard:
            restarted = evaluate.PracticeHarness(CONFIG, client, guard=guard, exclusive_reserved=True)
            with pytest.raises(support.HarnessError, match="previous_task_not_confirmed_terminal"):
                await restarted.run_case(FIXTURE)
            assert len(service.requests) == calls


async def test_interruption_after_acceptance_keeps_task_id_and_skips_future_cases(private_root):
    clock, service = Clock(), Service()

    def interrupt(event, report):
        if event == "task_accepted":
            raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=private_root / "state") as guard:
            harness = evaluate.PracticeHarness(CONFIG, client, guard=guard, exclusive_reserved=True,
                monotonic=clock.now, sleep=clock.sleep, on_event=interrupt)
            reports = await harness.run_suite([{**FIXTURE, "id": "E1"}, {**FIXTURE, "id": "E3"}], 1)
            assert reports[0].error_code == "interrupted"
            assert reports[1].outcome == "SKIPPED" and reports[1].error_code == "interrupted"
            assert guard.pending()["task_id"] == service.task_id
    assert sum(request.url.path == "/tasks" for request in service.requests) == 1
    assert not any(request.url.path == "/__check" for request in service.requests)


@pytest.mark.parametrize("state,expected", [("terminada", "clear"), ("fallida", "clear"), ("en_curso", "pending")])
async def test_reconcile_only_reads_health_and_known_task_and_never_resets(
    private_root, monkeypatch, capsys, state, expected
):
    calls = []
    original_client = httpx.AsyncClient
    task_id = str(uuid4())

    def respond(request):
        calls.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"estado": state, "resumen": "CANARY_PRIVATE_ANSWER"})

    monkeypatch.setattr(evaluate.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs))
    with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=private_root / "state") as guard:
        guard.mark("task_pending", support.CaseReport("E1", task_id=task_id))
        if expected == "pending":
            with pytest.raises(support.HarnessError, match="reconciliation_still_pending"):
                await evaluate.reconcile(CONFIG, guard)
            assert guard.pending()["task_id"] == task_id
        else:
            assert await evaluate.reconcile(CONFIG, guard) == 0
            assert guard.pending() is None
    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/health"), ("GET", "/tasks/" + task_id)]
    assert "CANARY_PRIVATE_ANSWER" not in capsys.readouterr().out


@pytest.mark.parametrize("condition", ["unknown_task", "different_entrypoint", "http_error", "malformed_terminal"])
async def test_reconcile_preserves_block_on_uncertainty(private_root, monkeypatch, condition):
    calls = []
    original_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if condition == "http_error":
            return httpx.Response(404, text="CANARY_PASSWORD")
        return httpx.Response(200, json={"estado": "finished", "resumen": "CANARY_PASSWORD"})

    monkeypatch.setattr(evaluate.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs))
    directory = private_root / "state"
    with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=directory) as guard:
        guard.mark("task_pending", support.CaseReport("E1", task_id=None if condition == "unknown_task" else str(uuid4())))
    changed = replace(CONFIG, entrypoint="https://other-agent.example") if condition == "different_entrypoint" else CONFIG
    with support.DesktopGuard(changed.desktop_ws, changed.entrypoint, directory=directory) as guard:
        before = guard.path.read_bytes()
        with pytest.raises(support.HarnessError):
            await evaluate.reconcile(changed, guard)
        assert guard.path.read_bytes() == before
    assert all(request.method == "GET" for request in calls)
    if condition in {"unknown_task", "different_entrypoint"}:
        assert calls == []


def test_report_incremental_metrics_missing_cost_and_archival(private_root):
    plan = [{"case_id": "E1", "repetition": 1}, {"case_id": "E3", "repetition": 1}]
    path = private_root / "result.json"
    writer = support.ReportWriter(path, plan, suite_id=str(uuid4()), event_path=private_root / "events.jsonl")
    running = support.CaseReport("E1", started_at="2026-10-10T00:00:00Z", task_id=str(uuid4()))
    writer.event("task_accepted", running)
    snapshot = json.loads(path.read_text())
    assert snapshot["current_case"]["outcome"] is None and snapshot["reports"] == []
    failed = support.CaseReport("E1", outcome="ERROR", started_at=running.started_at,
                               error_code="task_timeout", task_observation_seconds=300)
    writer.completed(failed)
    writer.completed(support.CaseReport("E3", outcome="SKIPPED", error_code="blocked_by_previous_case"))
    writer.finish("blocked", "task_timeout")
    snapshot = json.loads(path.read_text())
    assert snapshot["counts"] == {"PASS": 0, "FAIL": 0, "ERROR": 1, "SKIPPED": 1}
    assert snapshot["total_executed"] == 1
    assert snapshot["metrics"]["task_observation_seconds"]["maximum"] == 300
    assert snapshot["metrics"]["acceptance_seconds"]["missing"] == 1
    assert snapshot["reports"][0]["cost_usd"] is None
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(writer.event_path.stat().st_mode) == 0o600
    previous = path.read_bytes()
    support.ReportWriter(path, plan, suite_id=str(uuid4()))
    assert any(archive.read_bytes() == previous for archive in private_root.glob("previous-*.json"))


def test_report_rejects_public_paths_journal_collisions_and_existing_event_file(private_root):
    with pytest.raises(support.HarnessError, match="report_must_be_private"):
        support.ReportWriter(private_root.parents[1] / "public.json", [], suite_id=str(uuid4()))
    with pytest.raises(support.HarnessError, match="report_must_be_private"):
        support.ReportWriter(private_root / "state" / "collision.json", [], suite_id=str(uuid4()))
    events = private_root / "exists.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("previous\n")
    with pytest.raises(support.HarnessError, match="report_exists"):
        support.ReportWriter(private_root / "result.json", [], suite_id=str(uuid4()), event_path=events)
    assert events.read_text() == "previous\n"


@pytest.mark.parametrize("collision", ["report", "details"])
def test_report_rejects_actual_event_path_collisions(private_root, collision):
    path = private_root / "result.json"
    events = path if collision == "report" else path.with_suffix(".details.jsonl")
    with pytest.raises(support.HarnessError, match="report_must_be_private"):
        support.ReportWriter(path, [], suite_id=str(uuid4()), event_path=events, details=True)


@pytest.mark.parametrize("scenario,exit_code,suite_status,outcome,journal_status", [
    ("success", 0, "completed", "PASS", "clear"),
    ("failed_check", 1, "completed", "FAIL", "clear"),
    ("uncertain_acceptance", 2, "blocked", "ERROR", "pending"),
])
async def test_cli_execute_default_report_options_with_mock_http_and_no_secret_output(
    private_root, fixtures, monkeypatch, capsys, scenario, exit_code, suite_status, outcome, journal_status
):
    service = Service(bad_check=scenario == "failed_check", uncertain_accept=scenario == "uncertain_acceptance")
    clock = Clock()
    original_client, original_harness = httpx.AsyncClient, evaluate.PracticeHarness
    monkeypatch.setattr(evaluate, "load_config", lambda *args: CONFIG)
    monkeypatch.setattr(evaluate, "load_trace_resolver", lambda *args: support.TraceResolver())
    monkeypatch.setattr(evaluate.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(service), **kwargs))
    monkeypatch.setattr(evaluate, "PracticeHarness", lambda *args, **kwargs: original_harness(
        *args, monotonic=clock.now, sleep=clock.sleep, **kwargs))
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda *args: None)
    monkeypatch.setattr(loop, "remove_signal_handler", lambda *args: None)
    destination = private_root / "cli-default.json"
    args = evaluate.build_parser().parse_args(["--execute", "--exclusive-desktop", "--cases", "E3",
        "--repetitions", "1", "--fixtures", str(fixtures), "--report", str(destination)])
    assert await evaluate.main(args) == exit_code
    report = json.loads(destination.read_text())
    assert report["status"] == suite_status and report["counts"][outcome] == 1
    assert report["current_case"] is None
    assert report["reports"][0]["cost_usd"] is None
    journal = next((private_root / "state").glob("*.json"))
    assert json.loads(journal.read_text())["status"] == journal_status
    output = capsys.readouterr().out + destination.read_text()
    for secret in (CONFIG.password, CONFIG.team_token, CONFIG.desktop_token, CONFIG.desktop_ws,
                   CONFIG.desktop_base, CONFIG.entrypoint, FIXTURE["instruccion"], "respuesta: 45"):
        assert secret not in output


async def test_readiness_deadline_blocks_admission_and_preserves_reset_journal(private_root):
    clock, service = Clock(), Service()

    def not_ready(request):
        if request.url.host == "desktop.example" and request.url.path == "/health":
            return httpx.Response(503, text="CANARY_PASSWORD")
        return service(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(not_ready)) as client:
        with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=private_root / "state") as guard:
            harness = evaluate.PracticeHarness(CONFIG, client, guard=guard, exclusive_reserved=True,
                monotonic=clock.now, sleep=clock.sleep, desktop_ready_timeout_seconds=3,
                desktop_settle_seconds=1)
            result = await harness.run_case(FIXTURE)
            assert result.error_code == "desktop_not_ready"
            assert result.desktop_ready_wait_seconds == 3
            assert guard.pending()["phase"] == "desktop_ready_wait"
    assert not any(request.url.path == "/tasks" for request in service.requests)


async def test_timing_metrics_separate_setup_acceptance_polling_check_and_case_wall():
    clock, service = Clock(), Service()

    def timed(request):
        delay = {"/__reset": 1, "/tasks": 0.5, "/__check": 3}.get(request.url.path, 0)
        clock.value += delay
        return service(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timed)) as client:
        harness = evaluate.PracticeHarness(CONFIG, client, exclusive_reserved=True,
            monotonic=clock.now, sleep=clock.sleep, desktop_settle_seconds=2)
        result = await harness.run_case(FIXTURE)
    assert result.reset_duration_seconds == 1
    assert result.desktop_ready_wait_seconds == 2
    assert result.acceptance_seconds == 0.5
    assert result.terminal_detected_seconds == result.duration_seconds == 5.5
    assert result.check_duration_seconds == 3
    assert result.case_wall_seconds == 11.5
    assert result.closure_basis == "api_terminal_contract"
    assert result.closure_confirmed is None and result.actions_after_close is None


async def test_optional_trace_failure_does_not_abort_accepted_task_or_invent_cost():
    class Unavailable:
        def resolve(self, task_id):
            raise RuntimeError("CANARY_TEAM_TOKEN")

    clock, service = Clock(), Service()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        harness = evaluate.PracticeHarness(CONFIG, client, exclusive_reserved=True,
            monotonic=clock.now, sleep=clock.sleep, trace_resolver=Unavailable())
        result = await harness.run_case(FIXTURE)
    assert result.outcome == "PASS" and result.telemetry_status == "unavailable"
    assert result.cost_usd is None and result.cost_status == "unavailable"
    assert "CANARY_TEAM_TOKEN" not in json.dumps(asdict(result))


async def test_report_failure_before_reset_is_fail_closed(private_root):
    clock, service = Clock(), Service()

    def cannot_write(event, report):
        if event == "reset_started":
            raise support.HarnessError("report_write_failed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with support.DesktopGuard(CONFIG.desktop_ws, CONFIG.entrypoint, directory=private_root / "state") as guard:
            harness = evaluate.PracticeHarness(CONFIG, client, guard=guard, exclusive_reserved=True,
                monotonic=clock.now, sleep=clock.sleep, on_event=cannot_write)
            report = await harness.run_case(FIXTURE)
            assert report.error_code == "report_write_failed"
            assert guard.pending()["phase"] == "reset_in_flight"
    assert [request.url.path for request in service.requests] == ["/health"]


def test_trace_resolver_never_instantiates_or_exports_and_cost_stays_unknown(monkeypatch):
    import langfuse

    seeds = []

    class SDK:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Trace resolver must not create a client")

        @staticmethod
        def create_trace_id(*, seed):
            seeds.append(seed)
            return "a" * 32

    monkeypatch.setattr(langfuse, "Langfuse", SDK)
    task_id = str(uuid4())
    resolved = support.TraceResolver("https://us.cloud.langfuse.com", "project123").resolve(task_id)
    assert seeds == [task_id] and resolved["telemetry_status"] == "pending"
    assert resolved["trace_url"].endswith("/traces/" + "a" * 32)
    assert support.TraceResolver("https://private.invalid", "project123").resolve(task_id)["trace_url"] is None
    assert support.CaseReport("E1").cost_usd is None


async def test_e5_expected_failure_can_pass_and_untrusted_check_labels_are_redacted(capsys):
    def respond(request):
        if request.url.path.startswith("/tasks/"):
            return httpx.Response(200, json={"estado": "fallida", "resumen": "Identity ambiguous: ID-A; ID-B"})
        if request.url.path == "/__check":
            return httpx.Response(200, json={"pass": True, "checks": [{"nombre": "CANARY_PASSWORD", "ok": True}]})
        return service(request)

    service, clock = Service(), Clock()
    fixture = {**FIXTURE, "id": "E5", "estado_esperado": "fallida", "claves_razon": ["ID-A", "ID-B"]}
    fixture.pop("respuesta_esperada")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        console = support.ConsoleProgress(1)
        harness = evaluate.PracticeHarness(CONFIG, client, exclusive_reserved=True, monotonic=clock.now,
            sleep=clock.sleep, on_event=console)
        report = await harness.run_case(fixture)
    assert report.outcome == "PASS" and report.public_state == "fallida" and report.evaluation_pass is True
    assert report.app_checks == [{"name": "check_001", "ok": True}]
    output = capsys.readouterr().out
    assert "CANARY_PASSWORD" not in output + json.dumps(asdict(report))
    assert "Identity ambiguous" not in output


@pytest.mark.parametrize("body", [
    {"pass": True, "checks": []}, {"pass": True, "checks": [{"nombre": "state", "ok": False}]},
    {"pass": False, "checks": [{"nombre": "state", "ok": True}]},
    {"pass": True, "checks": [{"nombre": "state", "ok": "true"}]},
])
def test_inconsistent_or_empty_app_check_is_never_a_pass(body):
    with pytest.raises(support.HarnessError, match="invalid_check_response"):
        evaluate.PracticeHarness.app_result(body)
