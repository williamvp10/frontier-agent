import json
from dataclasses import asdict, replace
from uuid import uuid4

import httpx
import pytest

from scripts.evaluate import HarnessError, PracticeConfig, PracticeHarness, summary_checks

CONFIG = PracticeConfig(entrypoint="https://agent.example", desktop_base="https://desktop.example",
    desktop_ws="wss://desktop.example/websockify", password="CANARY_PASSWORD",
    desktop_token="CANARY_DESKTOP_TOKEN", team_token="CANARY_TEAM_TOKEN")
FIXTURE = {"id": "T1", "instruccion": "Count visible rows", "estado_esperado": "terminada",
           "respuesta_esperada": "45", "diff_esperado": [], "lista_blanca": []}


class Clock:
    value = 0

    def now(self):
        return self.value

    async def sleep(self, seconds):
        self.value += seconds


class Service:
    def __init__(self, *, running=False, bad_check=False, uncertain_accept=False):
        self.requests = []
        self.task_id = str(uuid4())
        self.running, self.bad_check, self.uncertain_accept = running, bad_check, uncertain_accept

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"ok": True})
        if path == "/__reset":
            return httpx.Response(200, json={"ok": True})
        if path == "/tasks":
            if self.uncertain_accept:
                raise httpx.ConnectError("CANARY_PASSWORD CANARY_TEAM_TOKEN", request=request)
            return httpx.Response(200, json={"task_id": self.task_id})
        if path.startswith("/tasks/"):
            return httpx.Response(200, json={"estado": "en_curso" if self.running else "terminada",
                                           "resumen": "respuesta: 45"})
        if path == "/__check":
            return httpx.Response(200, json={"pass": not self.bad_check,
                                           "checks": [{"nombre": "state", "ok": not self.bad_check}]})
        return httpx.Response(404)


async def test_fixture_is_never_sent_to_agent_and_tokens_are_separate():
    service, clock = Service(), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        harness = PracticeHarness(CONFIG, client, monotonic=clock.now, sleep=clock.sleep,
                                  exclusive_reserved=True)
        reports = await harness.run_suite([FIXTURE], repetitions=1)
    assert reports[0].passed
    task = next(req for req in service.requests if req.url.path == "/tasks")
    payload = json.loads(task.content)
    assert set(payload) == {"tarea", "escritorio"}
    assert payload["tarea"] == FIXTURE["instruccion"]
    assert "respuesta_esperada" not in payload and "diff_esperado" not in payload
    for request in service.requests:
        desktop = request.url.host == "desktop.example"
        assert request.url.host == ("desktop.example" if desktop else "agent.example")
        expected = CONFIG.desktop_token if desktop else CONFIG.team_token
        assert request.headers["Authorization"] == "Bearer " + expected
    check = next(req for req in service.requests if req.url.path == "/__check")
    assert json.loads(check.content) == FIXTURE
    report = json.dumps(asdict(reports[0]))
    assert all(secret not in report for secret in (CONFIG.password, CONFIG.desktop_token, CONFIG.team_token))
    assert "respuesta: 45" not in report


async def test_timeout_never_resets_next_case_or_calls_check():
    service, clock = Service(running=True), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        harness = PracticeHarness(CONFIG, client, monotonic=clock.now, sleep=clock.sleep,
                                   task_seconds=12, close_target_seconds=10, exclusive_reserved=True)
        reports = await harness.run_suite([FIXTURE, {**FIXTURE, "id": "T2"}], repetitions=1)
        with pytest.raises(HarnessError, match="previous_task_not_confirmed_terminal"):
            await harness.run_case(FIXTURE)
    assert len(reports) == 2 and reports[0].error_code == "task_timeout"
    assert reports[1].outcome == "SKIPPED" and reports[1].error_code == "blocked_by_previous_case"
    assert sum(req.url.path == "/__reset" for req in service.requests) == 1
    assert not any(req.url.path == "/__check" for req in service.requests)


async def test_uncertain_acceptance_stops_suite_and_redacts_exception():
    service, clock = Service(uncertain_accept=True), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        harness = PracticeHarness(CONFIG, client, monotonic=clock.now, sleep=clock.sleep,
                                  exclusive_reserved=True)
        reports = await harness.run_suite([FIXTURE], repetitions=3)
    assert len(reports) == 3 and all(r.outcome == "SKIPPED" for r in reports[1:])
    assert reports[0].error_code == "api_request_failed"
    assert "CANARY" not in repr(reports)
    assert sum(req.url.path == "/__reset" for req in service.requests) == 1


async def test_failed_app_check_prevents_false_success():
    service, clock = Service(bad_check=True), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        harness = PracticeHarness(CONFIG, client, monotonic=clock.now, sleep=clock.sleep,
                                  exclusive_reserved=True)
        result = await harness.run_case(FIXTURE)
    assert not result.passed
    assert result.checks["expected_state"] and not result.checks["app_state"]


async def test_terminal_allows_next_reset_only_after_check():
    service, clock = Service(), Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        harness = PracticeHarness(CONFIG, client, monotonic=clock.now, sleep=clock.sleep,
                                  exclusive_reserved=True)
        results = await harness.run_suite([FIXTURE], repetitions=2)
    assert all(result.passed for result in results)
    paths = [req.url.path for req in service.requests if req.url.path != "/health"]
    assert paths[0] == "/__reset" and paths[3] == "/__check" and paths[4] == "/__reset"


def test_wrong_desktop_target_and_redirect_credentials_are_rejected():
    with pytest.raises(HarnessError, match="desktop_target_mismatch"):
        replace(CONFIG, desktop_ws="wss://another.example/websockify").validate()
    with pytest.raises(HarnessError, match="invalid_config"):
        replace(CONFIG, entrypoint="https://secret@agent.example").validate()


@pytest.mark.parametrize("summary, good", [
    ("respuesta: 45", True), ("respuesta: total 45 registros", True),
    ("respuesta: cliente 7, tiene 45", False), ("respuesta: 450", False),
    ("Hay 45 registros", False), ("respuesta: 45\nrespuesta: 45", False),
])
def test_numeric_answer_requires_first_number_in_single_answer_line(summary, good):
    checks = summary_checks(FIXTURE, "terminada", summary)
    assert all(checks.values()) is good


def test_two_answers_order_and_reason_keys():
    fixture = {**FIXTURE, "respuesta_esperada": ["12", "Pereira"]}
    assert all(summary_checks(fixture, "terminada", "respuesta: 12; Pereira").values())
    assert not all(summary_checks(fixture, "terminada", "respuesta: Pereira; 12").values())
    assert not all(summary_checks(fixture, "terminada", "respuesta: 12 Pereira").values())
    reason = {**FIXTURE, "estado_esperado": "fallida", "claves_razon": ["ID-A", "ID-B"]}
    reason.pop("respuesta_esperada")
    assert all(summary_checks(reason, "fallida", "Identidad ambigua: ID-A; ID-B").values())
    assert not all(summary_checks(reason, "fallida", "Identidad ambigua").values())


async def test_acceptance_latency_is_measured_separately():
    clock = Clock()
    service = Service()

    def slow_accept(request):
        if request.url.path == "/tasks":
            clock.value += 2.5
        return service(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_accept)) as client:
        harness = PracticeHarness(CONFIG, client, monotonic=clock.now, sleep=clock.sleep,
                                  exclusive_reserved=True)
        result = await harness.run_case(FIXTURE)
    assert result.acceptance_seconds == 2.5
    assert result.duration_seconds == 7.5
    assert not result.passed and not result.checks["accepted_under_2s"]


async def test_redirect_is_not_followed_or_authenticated_to_other_host():
    requests = []

    def redirect(request):
        requests.append(request)
        if request.url.host == "agent.example" and request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(302, headers={"Location": "https://other.example/__reset"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        harness = PracticeHarness(CONFIG, client, exclusive_reserved=True)
        result = await harness.run_case(FIXTURE)
    assert result.error_code == "desktop_request_failed"
    assert len(requests) == 2
    assert requests[-1].url.host == "desktop.example"


async def test_no_reservation_blocks_before_any_request_or_reset():
    service = Service()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        harness = PracticeHarness(CONFIG, client)
        with pytest.raises(HarnessError, match="exclusive_reservation_required"):
            await harness.run_suite([FIXTURE], repetitions=1)
    assert service.requests == []
